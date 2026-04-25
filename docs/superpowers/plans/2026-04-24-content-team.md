# Content Team Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring up the Content Team (Content Manager + Content Writer + Content QA) thin-slice and, in the same drop, generalize the orchestrator's hardcoded "engineering_head is the only manager" assumption. Retire `TaskType`, move allow rules into the protocol, and generalize KB-delete/manage-agent from EH-only to any team manager.

**Architecture:** Team composition lives in `<runtime>/teams.yaml`; code owns only the `TeamsRegistry` abstraction. All manager-gated surfaces (orchestrator decisions, KB delete, manage-agent, capabilities prompt) go through registry lookups. Allow-rule prefixes move from a Python constant into `### Allow Rules` subsections under each manager's heading in `protocol/02-system-prompts-managers.md`.

**Tech Stack:** Python 3.11+ (3.13 in use) with `uv`, Pydantic v2, FastAPI daemon, SQLite (WAL), PyYAML for `teams.yaml`, pytest. No new third-party libraries.

**Source spec:** `docs/superpowers/specs/2026-04-24-content-team-design.md`.

**Working branch:** `worktree-content-team` under `.worktrees/content-team/`.

**Pre-flight note:** there is an uncommitted protocol cleanup carried over from the prior session (worktree is not clean). The very first action below commits that snapshot so every subsequent task starts from a known-clean tree.

---

## File Structure

**Created:**
- `src/orchestrator/teams.py` — `TeamManager` dataclass + `TeamsRegistry` class, `load()` / `save()` / mutation helpers.
- `tests/test_teams.py` — registry load/save, lookups, mutations, missing-file default.
- `tests/test_prompt_loader_allow_rules.py` — `### Allow Rules` parser.
- `tests/test_workspace_adapters_allow_rules.py` — baseline + extras composition in both syntaxes.
- `tests/daemon/test_tasks_team_routing.py` — `POST /tasks` with `team` routes to the right manager.
- `tests/daemon/test_kb_delete_team_managers.py` — CM + EH pass, worker rejected, founder wins.
- `tests/daemon/test_manage_agent_team_scoping.py` — CM enrolls into `content`, cross-team 403.
- `tests/orchestrator/test_run_step_content_team.py` — scripted CM → writer → QA PASS / REVISE / REJECT.
- `tests/orchestrator/test_run_step_cross_team_rejection.py` — CM delegating to `dev_agent` yields a feedback step, not a delegation.
- `tests/integration/test_content_team_e2e.py` — full daemon + fake-Claude flow through the Content Team.

**Modified:**
- `src/runtime.py` — add `teams_config_path` property; seed default yaml in `init()`.
- `src/models.py` — delete `TaskType`; remove `type` field from `TaskRecord`; change `team` default to `"engineering"`.
- `src/infrastructure/database.py` — update `tasks.team` DDL default to `'engineering'`; backfill legacy `'product_engineering'` rows; `UPDATE` preserves historical values.
- `src/infrastructure/audit_logger.py` — delete `log_cross_audit_stub` and its lone call site.
- `src/daemon/state.py` — add `teams: TeamsRegistry | None` and `teams_lock`.
- `src/daemon/app.py` (lifespan) — load registry on startup and after `/runtimes/use`.
- `src/daemon/routes/tasks.py` — replace `type: TaskType` with `team: str` on `SubmitTask`; unknown team → 400; default routing assigns `task.assigned_agent = manager_for_team(team).name`.
- `src/daemon/routes/kb.py` — drop `_TOPIC_FOR_TASK_TYPE`; derive topic from `task.team`; delete gate uses `registry.is_team_manager(agent)`.
- `src/daemon/routes/agents.py` — rename `_require_eh_auth` → `_require_team_manager_auth`; `manage-agent` enrollment adds the enrolled worker to the caller's team via registry; cross-team enrollment → 403.
- `src/orchestrator/prompt_loader.py` — register `content_manager` + `content_writer`; add `allow_rules_for(agent)` parser.
- `src/orchestrator/workspace_adapters.py` — delete `AGENT_EXTRA_ALLOWED_BASH_PREFIXES`; `allow_rules_for_agent` consults `prompt_loader.allow_rules_for`; update "Delete: engineering_head only" docstring line.
- `src/orchestrator/capabilities.py` — scope the prompt to the calling manager's team only.
- `src/orchestrator/run_step.py` — replace 5 hardcoded `"engineering_head"` sites with registry lookups; enforce same-team delegation.
- `src/cli.py` — `opc run`: replace `--task` flag with `--team`.
- `protocol/02-system-prompts-managers.md` — add `### Allow Rules` subsection under Engineering Head (4 `gh` bullets); under Content Manager (empty list, present for parser regularity).
- `protocol/06-knowledge-base.md` — "Delete: any team manager; founder via `--as-founder`".
- `CLAUDE.md` and `README.md` — sync to the new vocabulary.

---

## Task 0: Commit the carried-over protocol cleanup

**Why first:** the worktree has uncommitted changes carried over from the prior session (spec + protocol edits that scratched Agent Tools / Feishu). Subsequent TDD steps need a clean baseline so per-task diffs stay reviewable.

- [ ] **Step 1: Inspect what's uncommitted**

Run: `git -C /Users/tangbz/projects/my-opc/.worktrees/content-team status --short`
Expected: a list of modified/added files — at minimum the new spec under `docs/superpowers/specs/` and edits in `protocol/`.

- [ ] **Step 2: Run the existing test suite baseline**

Run: `cd /Users/tangbz/projects/my-opc/.worktrees/content-team && uv run pytest tests/ -q`
Expected: all unit tests pass on `main`'s current behavior. Record any pre-existing failures — these are not regressions from this plan and should not be "fixed" as part of a plan task.

- [ ] **Step 3: Commit the snapshot**

```bash
cd /Users/tangbz/projects/my-opc/.worktrees/content-team
git add docs/superpowers/specs/2026-04-24-content-team-design.md protocol/
git commit -m "chore(protocol): drop agent-tools/feishu scope, add content-team design spec"
```

Expected: one clean commit. `git status` should show no unstaged protocol or spec changes. (Leaves `docs/superpowers/plans/2026-04-24-content-team.md` uncommitted so the next phase's steps can continue to edit it.)

---

## Task 1: `TeamsRegistry` abstraction (pure code, no wiring)

**Files:**
- Create: `src/orchestrator/teams.py`
- Create: `tests/test_teams.py`

- [ ] **Step 1: Write failing tests for TeamsRegistry**

Create `tests/test_teams.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from src.orchestrator.teams import TeamManager, TeamsRegistry
from src.runtime import RuntimeDir


def _runtime(tmp_path: Path) -> RuntimeDir:
    return RuntimeDir.init(tmp_path / "rt")


def test_load_missing_file_returns_default_layout(tmp_path: Path) -> None:
    rt = _runtime(tmp_path)
    reg = TeamsRegistry.load(rt)
    assert reg.teams() == ["content", "engineering"]
    eng = reg.manager_for_team("engineering")
    assert eng.name == "engineering_head"
    assert eng.workers == ("product_manager", "dev_agent", "payment_agent", "qa_engineer")
    content = reg.manager_for_team("content")
    assert content.name == "content_manager"
    assert content.workers == ("content_writer", "content_qa")


def test_save_then_load_roundtrips(tmp_path: Path) -> None:
    rt = _runtime(tmp_path)
    reg = TeamsRegistry.load(rt)
    reg.save(rt)
    reloaded = TeamsRegistry.load(rt)
    assert reloaded.teams() == reg.teams()
    assert reloaded.manager_for_team("content").workers == reg.manager_for_team("content").workers


def test_lookup_helpers(tmp_path: Path) -> None:
    rt = _runtime(tmp_path)
    reg = TeamsRegistry.load(rt)
    assert reg.team_for_agent("dev_agent") == "engineering"
    assert reg.team_for_agent("content_writer") == "content"
    assert reg.team_for_agent("unknown_agent") is None
    assert reg.team_for_manager("engineering_head") == "engineering"
    assert reg.team_for_manager("content_manager") == "content"
    assert reg.team_for_manager("dev_agent") is None
    assert reg.is_team_manager("engineering_head")
    assert reg.is_team_manager("content_manager")
    assert not reg.is_team_manager("dev_agent")


def test_add_and_remove_worker_persists(tmp_path: Path) -> None:
    rt = _runtime(tmp_path)
    reg = TeamsRegistry.load(rt)
    reg.add_worker("content", "seo_agent")
    reloaded = TeamsRegistry.load(rt)
    assert "seo_agent" in reloaded.manager_for_team("content").workers
    reloaded.remove_worker("content", "seo_agent")
    again = TeamsRegistry.load(rt)
    assert "seo_agent" not in again.manager_for_team("content").workers


def test_add_worker_to_unknown_team_raises(tmp_path: Path) -> None:
    rt = _runtime(tmp_path)
    reg = TeamsRegistry.load(rt)
    with pytest.raises(KeyError):
        reg.add_worker("ops", "partner_liaison")


def test_manager_for_unknown_team_raises(tmp_path: Path) -> None:
    rt = _runtime(tmp_path)
    reg = TeamsRegistry.load(rt)
    with pytest.raises(KeyError):
        reg.manager_for_team("ops")


def test_all_agents_returns_managers_and_workers(tmp_path: Path) -> None:
    rt = _runtime(tmp_path)
    reg = TeamsRegistry.load(rt)
    agents = set(reg.all_agents())
    assert {"engineering_head", "content_manager", "dev_agent", "content_writer", "content_qa"} <= agents
```

- [ ] **Step 2: Run tests — expect failure**

Run: `uv run pytest tests/test_teams.py -v`
Expected: ImportError (`src.orchestrator.teams` does not exist).

- [ ] **Step 3: Implement `src/orchestrator/teams.py`**

Create `src/orchestrator/teams.py`:

```python
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
    def __init__(self, teams: dict[str, TeamManager]) -> None:
        self._teams = dict(teams)

    # ---- construction ----

    @classmethod
    def load(cls, runtime: RuntimeDir) -> "TeamsRegistry":
        path = runtime.teams_config_path
        if not path.exists():
            return cls._from_layout(DEFAULT_LAYOUT)
        raw = yaml.safe_load(path.read_text()) or {}
        layout = raw.get("teams") or {}
        if not layout:
            return cls._from_layout(DEFAULT_LAYOUT)
        return cls._from_layout(layout)

    @classmethod
    def _from_layout(cls, layout: dict[str, dict[str, object]]) -> "TeamsRegistry":
        teams: dict[str, TeamManager] = {}
        for team_name, entry in layout.items():
            manager = entry.get("manager")
            workers = tuple(entry.get("workers") or ())
            if not isinstance(manager, str) or not manager:
                raise ValueError(f"team {team_name!r} missing manager")
            teams[team_name] = TeamManager(name=manager, team=team_name, workers=workers)
        return cls(teams)

    # ---- persistence ----

    def save(self, runtime: RuntimeDir) -> None:
        path = runtime.teams_config_path
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
            if os.path.exists(tmp):
                os.unlink(tmp)
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

    # ---- mutation ----

    def add_worker(self, team: str, agent: str) -> None:
        if team not in self._teams:
            raise KeyError(team)
        m = self._teams[team]
        if agent in m.workers:
            return
        self._teams[team] = TeamManager(
            name=m.name, team=m.team, workers=tuple([*m.workers, agent]),
        )

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
```

Add the new property to `src/runtime.py` (inside class `RuntimeDir`, after `workspaces_dir`):

```python
    @property
    def teams_config_path(self) -> Path:
        return self._path / "teams.yaml"
```

- [ ] **Step 4: Run tests — expect pass**

Run: `uv run pytest tests/test_teams.py -v`
Expected: all 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/teams.py src/runtime.py tests/test_teams.py
git commit -m "feat(orchestrator): TeamsRegistry + teams.yaml persistence"
```

---

## Task 2: Seed `teams.yaml` on init; load into `DaemonState`

**Files:**
- Modify: `src/runtime.py:48-66` — have `init()` write default yaml if missing.
- Modify: `src/daemon/state.py:29-36` — add `teams` + `teams_lock` fields.
- Modify: `src/daemon/app.py` — load registry on startup and on `/runtimes/use`.
- Modify: `tests/test_runtime.py` (or create) — assert the file lands with the default layout.

- [ ] **Step 1: Write failing test for init seeding**

Add (or create) `tests/test_runtime.py` containing:

```python
from __future__ import annotations

from pathlib import Path

import yaml

from src.runtime import RuntimeDir


def test_init_seeds_default_teams_yaml(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt")
    assert rt.teams_config_path.exists()
    data = yaml.safe_load(rt.teams_config_path.read_text())
    assert set(data["teams"].keys()) == {"engineering", "content"}
    assert data["teams"]["engineering"]["manager"] == "engineering_head"
    assert data["teams"]["content"]["manager"] == "content_manager"


def test_init_is_idempotent_for_teams_yaml(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt")
    rt.teams_config_path.write_text("teams:\n  custom:\n    manager: custom_mgr\n    workers: []\n")
    # Second init must not overwrite an existing teams.yaml.
    RuntimeDir.init(tmp_path / "rt")
    data = yaml.safe_load(rt.teams_config_path.read_text())
    assert "custom" in data["teams"]
```

- [ ] **Step 2: Run tests — expect failure**

Run: `uv run pytest tests/test_runtime.py -v`
Expected: `AssertionError` — `teams.yaml` does not yet exist after `init()`.

- [ ] **Step 3: Seed teams.yaml in `RuntimeDir.init`**

Edit `src/runtime.py` — replace the body of `init` (roughly lines 48-66) to write a default teams.yaml only if missing:

```python
    @classmethod
    def init(cls, path: Path) -> RuntimeDir:
        """Create a runtime directory at *path* (idempotent)."""
        instance = cls(path)
        instance.root.mkdir(parents=True, exist_ok=True)
        if not instance.marker_file.exists():
            instance.marker_file.write_text("")
        instance.workspaces_dir.mkdir(parents=True, exist_ok=True)
        if not instance.teams_config_path.exists():
            # Deferred import: teams.py imports RuntimeDir, so the import lives
            # inside the function to avoid a cycle at module-load time.
            from src.orchestrator.teams import TeamsRegistry, DEFAULT_LAYOUT
            TeamsRegistry._from_layout(DEFAULT_LAYOUT).save(instance)
        return instance
```

- [ ] **Step 4: Run runtime tests — expect pass**

Run: `uv run pytest tests/test_runtime.py tests/test_teams.py -v`
Expected: all pass.

- [ ] **Step 5: Wire `DaemonState` to hold the registry**

Edit `src/daemon/state.py`:

Replace the imports section to add `TeamsRegistry`:

```python
from src.orchestrator.teams import TeamsRegistry
```

Add to the `DaemonState` dataclass body (after `kb_lock`):

```python
    teams_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    teams: TeamsRegistry | None = None
```

Update `from_runtime` to populate `teams`:

```python
    @classmethod
    def from_runtime(cls, runtime: RuntimeDir, settings: Settings) -> "DaemonState":
        return cls(
            runtime=runtime,
            db=Database(runtime.db_path),
            settings=settings,
            teams=TeamsRegistry.load(runtime),
        )
```

- [ ] **Step 6: Reload registry on `/runtimes/use`**

Edit `src/daemon/routes/runtimes.py`. Find the handler that swaps the active runtime (look for where `app.state.daemon = DaemonState.from_runtime(...)` is set) — no code changes needed if it already re-builds `DaemonState` via `from_runtime`, because Step 5 made that populate `teams`. If the handler sets `runtime` + `db` on an existing state instead, follow the same pattern by assigning `state.teams = TeamsRegistry.load(runtime)`.

Run: `grep -n "from_runtime\|state.runtime = \|state.db = " src/daemon/routes/runtimes.py`
Expected: the route already rebuilds DaemonState via `from_runtime`, so no further edit. If not, add the `state.teams = ...` assignment next to any place where `state.runtime` is reassigned.

- [ ] **Step 7: Add a daemon-level test that the registry is populated**

Extend `tests/daemon/test_runtimes_route.py` (or add a new file if one doesn't exist at that path) with a test that after `POST /runtimes/init`, `state.teams` is non-None and `state.teams.manager_for_team("engineering").name == "engineering_head"`. Use the in-process test client pattern already in use in `tests/daemon/`.

Run: `uv run pytest tests/daemon/ -v -k teams`
Expected: the new test passes. (If `tests/daemon/test_runtimes_route.py` doesn't exist yet, pattern-match an existing `tests/daemon/test_*.py` file for the app-fixture shape and create one for this test.)

- [ ] **Step 8: Commit**

```bash
git add src/runtime.py src/daemon/state.py src/daemon/routes/runtimes.py tests/test_runtime.py tests/daemon/
git commit -m "feat(daemon): seed teams.yaml on init and load TeamsRegistry into DaemonState"
```

---

## Task 3: Move `gh` allow rules from code to protocol

**Files:**
- Modify: `protocol/02-system-prompts-managers.md:68` — add `### Allow Rules` subsection after the Engineering Head fenced block.
- Modify: `protocol/02-system-prompts-managers.md:67` — add an (empty) `### Allow Rules` subsection after the Content Manager fenced block.
- Modify: `src/orchestrator/prompt_loader.py` — add `allow_rules_for(agent_name: str) -> tuple[str, ...]`.
- Modify: `src/orchestrator/workspace_adapters.py:23-60` — delete `AGENT_EXTRA_ALLOWED_BASH_PREFIXES`; delegate to `prompt_loader.allow_rules_for`.
- Create: `tests/test_prompt_loader_allow_rules.py`
- Create: `tests/test_workspace_adapters_allow_rules.py`

- [ ] **Step 1: Write failing test for the parser**

Create `tests/test_prompt_loader_allow_rules.py`:

```python
from __future__ import annotations

from pathlib import Path

from src.orchestrator.prompt_loader import allow_rules_for


def _write(path: Path, contents: str) -> None:
    path.write_text(contents)


def test_parses_allow_rules_bullets(tmp_path: Path) -> None:
    md = tmp_path / "02-system-prompts-managers.md"
    _write(md, """
## Engineering Head

```
role text
```

### Allow Rules

Beyond the baseline `opc *` grant, this agent may run:

- `gh pr close`
- `gh pr comment`
- `gh issue close`
- `gh issue comment`

---

## Content Manager

```
role text
```

### Allow Rules

No additional grants.

---
""".lstrip())
    # The loader must accept a protocol_dir pointing at tmp_path.
    assert allow_rules_for(tmp_path, "engineering_head") == (
        "gh pr close", "gh pr comment", "gh issue close", "gh issue comment",
    )
    assert allow_rules_for(tmp_path, "content_manager") == ()


def test_missing_subsection_returns_empty(tmp_path: Path) -> None:
    md = tmp_path / "02-system-prompts-managers.md"
    _write(md, """
## Engineering Head

```
role text
```

---
""".lstrip())
    assert allow_rules_for(tmp_path, "engineering_head") == ()


def test_unknown_agent_returns_empty(tmp_path: Path) -> None:
    md = tmp_path / "02-system-prompts-managers.md"
    _write(md, "## Engineering Head\n\n```\nx\n```\n")
    assert allow_rules_for(tmp_path, "nobody") == ()
```

- [ ] **Step 2: Run — expect failure**

Run: `uv run pytest tests/test_prompt_loader_allow_rules.py -v`
Expected: `ImportError: cannot import name 'allow_rules_for' from 'src.orchestrator.prompt_loader'`.

- [ ] **Step 3: Implement the parser**

Append to `src/orchestrator/prompt_loader.py`:

```python
_BULLET_RE = re.compile(r"^-\s+`?([^`\n]+?)`?\s*$")


def allow_rules_for(protocol_dir: Path, agent_name: str) -> tuple[str, ...]:
    """Extract the bullet list under the ``### Allow Rules`` subsection
    inside an agent's role section. Returns ``()`` when the subsection
    is absent or empty.

    The agent's role section starts at ``## <Heading>`` and ends at the
    next ``## `` heading or ``---`` divider.  Inside that range we find
    ``### Allow Rules``, then collect ``- <prefix>`` bullets until the
    next heading.
    """
    source = _AGENT_SOURCES.get(agent_name)
    if source is None:
        return ()
    filename, heading = source
    filepath = protocol_dir / filename
    if not filepath.exists():
        return ()

    text = filepath.read_text()
    head_re = re.compile(rf"^## {re.escape(heading)}\s*$", re.MULTILINE)
    head = head_re.search(text)
    if head is None:
        return ()

    # Section ends at next `## ` heading or `---` divider.
    end_re = re.compile(r"^(## |---\s*$)", re.MULTILINE)
    end = end_re.search(text, head.end())
    section = text[head.end(): end.start() if end else len(text)]

    sub_re = re.compile(r"^### Allow Rules\s*$", re.MULTILINE)
    sub = sub_re.search(section)
    if sub is None:
        return ()

    tail_re = re.compile(r"^(## |### |---\s*$)", re.MULTILINE)
    tail = tail_re.search(section, sub.end())
    body = section[sub.end(): tail.start() if tail else len(section)]

    rules: list[str] = []
    for line in body.splitlines():
        m = _BULLET_RE.match(line)
        if m:
            rules.append(m.group(1).strip())
    return tuple(rules)
```

Add `from pathlib import Path` if not already imported.

- [ ] **Step 4: Run parser tests — expect pass**

Run: `uv run pytest tests/test_prompt_loader_allow_rules.py -v`
Expected: 3/3 pass.

- [ ] **Step 5: Edit the protocol file**

Open `protocol/02-system-prompts-managers.md`. After the closing ``` of the Content Manager fenced block (line 66 currently) and BEFORE the `---` divider on line 68, insert:

```markdown

### Allow Rules

No additional grants beyond `opc *`.

```

After the closing ``` of the Engineering Head fenced block and before the `---` divider that follows, insert:

```markdown

### Allow Rules

Beyond the baseline `opc *` grant, this agent may run:

- `gh pr close`
- `gh pr comment`
- `gh issue close`
- `gh issue comment`

```

Verify with:

```bash
grep -n "### Allow Rules" protocol/02-system-prompts-managers.md
```

Expected: exactly two hits — one under Content Manager, one under Engineering Head.

- [ ] **Step 6: Write failing test for workspace_adapters wiring**

Create `tests/test_workspace_adapters_allow_rules.py`:

```python
from __future__ import annotations

from src.config import Settings
from src.orchestrator.workspace_adapters import allow_rules_for_agent


def test_baseline_only_for_unknown_agent() -> None:
    s = Settings()
    settings_rules = allow_rules_for_agent(s, "nobody", cli=False)
    cli_rules = allow_rules_for_agent(s, "nobody", cli=True)
    assert settings_rules == ["Bash(opc:*)"]
    assert cli_rules == ["Bash(opc *)"]


def test_engineering_head_gets_gh_extras_from_protocol() -> None:
    s = Settings()
    settings_rules = allow_rules_for_agent(s, "engineering_head", cli=False)
    assert "Bash(opc:*)" in settings_rules
    assert "Bash(gh pr close:*)" in settings_rules
    assert "Bash(gh issue close:*)" in settings_rules


def test_content_manager_has_no_extras() -> None:
    s = Settings()
    assert allow_rules_for_agent(s, "content_manager", cli=False) == ["Bash(opc:*)"]
```

- [ ] **Step 7: Run — expect failure**

Run: `uv run pytest tests/test_workspace_adapters_allow_rules.py -v`
Expected: the existing `allow_rules_for_agent(agent_name, *, cli)` signature doesn't accept `Settings`. Fails with TypeError.

- [ ] **Step 8: Rewire `allow_rules_for_agent`**

Edit `src/orchestrator/workspace_adapters.py`:

Delete the `AGENT_EXTRA_ALLOWED_BASH_PREFIXES` dict (lines 23-33) and its docstring block above.

Replace `allow_rules_for_agent` (lines 49-60) with:

```python
from src.orchestrator import prompt_loader


def allow_rules_for_agent(
    settings: Settings, agent_name: str | None, *, cli: bool,
) -> list[str]:
    """Build the Bash allow-rule list for ``agent_name``.

    Baseline ``opc`` is always included (the agent-callback channel).
    Additional prefixes come from the ``### Allow Rules`` subsection
    of the agent's role in the protocol markdown. See the spec at
    docs/superpowers/specs/2026-04-24-content-team-design.md §
    "Protocol-driven allow rules".
    """
    rules = [_format_allow_rule("opc", cli=cli)]
    if agent_name is None:
        return rules
    for prefix in prompt_loader.allow_rules_for(
        settings.get_protocol_dir(), agent_name,
    ):
        rules.append(_format_allow_rule(prefix, cli=cli))
    return rules
```

Update `build_settings_json` callers: every call to `allow_rules_for_agent(agent_name, cli=...)` now needs a `settings` first positional — `build_settings_json` accepts `settings` as its first positional (add it) and forwards. Mirror the change in `ClaudeWorkspaceAdapter.write_settings_json` and `ClaudeExecutor.run` (wherever the CLI `--allowedTools` is assembled).

Run: `grep -rn "allow_rules_for_agent" src/` to find all callers — each must be updated to thread the `Settings` object.

- [ ] **Step 9: Run all impacted tests — expect pass**

Run: `uv run pytest tests/test_workspace_adapters_allow_rules.py tests/ -q -k "allow or workspace or settings" -x`
Expected: pass. Also run `uv run pytest tests/ -q` briefly to catch any broken caller — there will be a few; fix them inline (they'll all be `allow_rules_for_agent(name, cli=...)` missing the `settings` positional).

- [ ] **Step 10: Update CLAUDE.md reference**

Edit `CLAUDE.md`: in the "Agent permission model" section, remove the `AGENT_EXTRA_ALLOWED_BASH_PREFIXES` mention and point to the protocol file instead. Specifically, replace the two `AGENT_EXTRA_ALLOWED_BASH_PREFIXES` references with "the `### Allow Rules` subsection of each manager's role in `protocol/02-system-prompts-managers.md`".

- [ ] **Step 11: Commit**

```bash
git add protocol/02-system-prompts-managers.md src/orchestrator/prompt_loader.py src/orchestrator/workspace_adapters.py tests/test_prompt_loader_allow_rules.py tests/test_workspace_adapters_allow_rules.py CLAUDE.md
git commit -m "refactor: move gh allow-rule prefixes from code constant to protocol markdown"
```

---

## Task 3b: Dynamic-agent allow rules on `agent_enrollments`

**Why separate:** the protocol parser (Task 3) covers built-in agents; dynamic agents (enrolled via `manage-agent`) need the same allow-rule machinery but source the prefixes from the DB row. Per spec §"Protocol-driven allow rules" §"Dynamic agents".

**Files:**
- Modify: `src/infrastructure/database.py` — add `allow_rules TEXT DEFAULT '[]'` column on `agent_enrollments`.
- Modify: `src/daemon/routes/agents.py` — `ManageAgentBody` accepts `allow_rules: list[str] | None`; stored on the enrollment row.
- Modify: `src/orchestrator/prompt_loader.py` or `src/orchestrator/workspace_adapters.py` — `allow_rules_for_agent` consults DB for dynamic agents *before* falling back to protocol parsing.
- Extend: `tests/test_workspace_adapters_allow_rules.py` with a dynamic-agent case.

- [ ] **Step 1: Write failing test for dynamic allow rules**

Extend `tests/test_workspace_adapters_allow_rules.py`:

```python
def test_dynamic_agent_uses_db_allow_rules(tmp_path, monkeypatch):
    # Seed a runtime + DB with an enrolled dynamic agent declaring allow_rules.
    from src.runtime import RuntimeDir
    from src.infrastructure.database import Database

    rt = RuntimeDir.init(tmp_path / "rt")
    db = Database(rt.db_path)
    db.insert_agent_enrollment(
        name="seo_bot",
        description="d",
        system_prompt="s",
        repos={},
        executor="claude",
        allow_rules=["curl https://api.example.com"],
    )
    db.update_agent_enrollment_status("seo_bot", "approved")

    s = Settings()
    rules = allow_rules_for_agent(s, "seo_bot", cli=False, db=db)
    assert "Bash(opc:*)" in rules
    assert "Bash(curl https://api.example.com:*)" in rules
```

Run: `uv run pytest tests/test_workspace_adapters_allow_rules.py::test_dynamic_agent_uses_db_allow_rules -v`
Expected: fail — `allow_rules_for_agent` signature does not yet accept `db`.

- [ ] **Step 2: Add the DB column**

Edit `src/infrastructure/database.py`:

In the `agent_enrollments` CREATE TABLE (lines 107-116), add `allow_rules TEXT NOT NULL DEFAULT '[]'`.

In the idempotent migrations block, add:

```python
"ALTER TABLE agent_enrollments ADD COLUMN allow_rules TEXT NOT NULL DEFAULT '[]'",
```

(Add it to the same `for ddl in (...)` loop that already wraps duplicate-column `OperationalError`.)

Update `insert_agent_enrollment` to accept and store `allow_rules: list[str]` (serialize to JSON); update the reader (`get_enrollment`) to deserialize.

- [ ] **Step 3: Consult DB in `allow_rules_for_agent`**

Edit `src/orchestrator/workspace_adapters.py`:

```python
def allow_rules_for_agent(
    settings: Settings, agent_name: str | None, *, cli: bool,
    db: Database | None = None,
) -> list[str]:
    rules = [_format_allow_rule("opc", cli=cli)]
    if agent_name is None:
        return rules

    # Dynamic agents first — the DB is authoritative for enrolled agents.
    prefixes: list[str] = []
    if db is not None:
        enrollment = db.get_enrollment(agent_name)
        if enrollment is not None:
            prefixes = list(enrollment.get("allow_rules") or ())

    # Built-ins / fallback — parse the protocol markdown.
    if not prefixes:
        prefixes = list(prompt_loader.allow_rules_for(
            settings.get_protocol_dir(), agent_name,
        ))

    for prefix in prefixes:
        rules.append(_format_allow_rule(prefix, cli=cli))
    return rules
```

Thread `state.db` through every caller — `ClaudeWorkspaceAdapter.write_settings_json`, `ClaudeExecutor.run`, and anywhere else that called the old signature.

- [ ] **Step 4: Surface on `ManageAgentBody`**

Edit `src/daemon/routes/agents.py`:

Add to `ManageAgentBody`:

```python
allow_rules: list[str] | None = None
```

In the enroll branch, forward `body.allow_rules or []` to `insert_agent_enrollment`.

Update `protocol/skills/manage-agent/SKILL.md` to document the optional field.

- [ ] **Step 5: Run — expect pass**

Run: `uv run pytest tests/test_workspace_adapters_allow_rules.py -v`
Expected: all 4 tests (3 original + 1 new) pass.

- [ ] **Step 6: Commit**

```bash
git add src/infrastructure/database.py src/orchestrator/workspace_adapters.py src/daemon/routes/agents.py protocol/skills/manage-agent/SKILL.md tests/test_workspace_adapters_allow_rules.py
git commit -m "feat(agents): dynamic-agent allow_rules stored on agent_enrollments"
```

---

## Task 4: Retire `TaskType`; add `--team` to `opc run`

**Files:**
- Modify: `src/models.py:24-28, 47-65` — delete `TaskType`; remove `type` field from `TaskRecord`; change `team` default to `"engineering"`.
- Modify: `src/infrastructure/database.py:57` — change `tasks.team` DDL default to `'engineering'`; add one-shot remap for legacy `'product_engineering'`.
- Modify: `src/infrastructure/database.py` — drop `type` from the `insert_task` INSERT column list; drop `type` from the `_row_to_task` hydration.
- Modify: `src/infrastructure/audit_logger.py:111` — delete `log_cross_audit_stub` and remove its call site.
- Modify: `src/daemon/routes/tasks.py:18, 29-48` — replace `type: TaskType` with `team: str`; validate against `state.teams`; unknown team 400.
- Modify: `src/daemon/routes/kb.py:22, 202-206, 249` — drop `_TOPIC_FOR_TASK_TYPE`; derive topic from `task.team`.
- Modify: `src/cli.py` — `opc run`: swap `--task` → `--team`.
- Modify: `tests/` — every test that passes `type=TaskType.X` gets updated to pass `team=".."` (none of the current tests should pass both).

- [ ] **Step 1: Write failing test for tasks-route team routing**

Create `tests/daemon/test_tasks_team_routing.py`:

```python
from __future__ import annotations

from fastapi.testclient import TestClient

# Import the existing test fixture that boots an in-process daemon with a
# temp runtime. Conventions vary across tests/daemon/ — grep for an
# existing `client` fixture or a `make_app()` helper and reuse it.
from tests.daemon.conftest import client  # type: ignore  # noqa: F401


def test_submit_with_engineering_team(client: TestClient) -> None:
    resp = client.post("/tasks", json={"team": "engineering", "brief": "x"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["assigned_agent"] == "engineering_head"
    assert body["team"] == "engineering"


def test_submit_with_content_team(client: TestClient) -> None:
    resp = client.post("/tasks", json={"team": "content", "brief": "y"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["assigned_agent"] == "content_manager"
    assert body["team"] == "content"


def test_submit_with_unknown_team_400s(client: TestClient) -> None:
    resp = client.post("/tasks", json={"team": "ops", "brief": "z"})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "unknown_team"


def test_submit_without_team_defaults_to_engineering(client: TestClient) -> None:
    resp = client.post("/tasks", json={"brief": "default"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["team"] == "engineering"
```

If `tests/daemon/conftest.py` does not already expose a `client` fixture, pattern-match from an existing daemon test file — specifically the `app` fixture setup used in any `tests/daemon/test_*.py` file — and bring that into `conftest.py` once so all daemon tests share it.

- [ ] **Step 2: Run tests — expect failure**

Run: `uv run pytest tests/daemon/test_tasks_team_routing.py -v`
Expected: 422 validation errors (current `SubmitTask` has `type: TaskType`, not `team`).

- [ ] **Step 3: Delete `TaskType` and tighten `TaskRecord`**

Edit `src/models.py`:

- Remove lines 24-28 (the `TaskType` enum).
- In `TaskRecord` (lines 47-65), delete the `type: TaskType` line.
- Change `team: str = "product_engineering"` to `team: str = "engineering"`.

- [ ] **Step 4: Update the tasks table DDL + data remap**

Edit `src/infrastructure/database.py`:

In `_create_tables` tasks DDL (line 52-66), drop the `type TEXT NOT NULL,` line entirely, and change `team TEXT NOT NULL DEFAULT 'product_engineering'` to `team TEXT NOT NULL DEFAULT 'engineering'`.

**Important note on the `type` column:** `CREATE TABLE IF NOT EXISTS` is a no-op when the table already exists. For already-initialized runtimes, the `type` column stays in place (SQLite has no easy `DROP COLUMN`). That's fine — nothing reads it anymore. Do not attempt a table rebuild; it's not worth the risk.

In the idempotent migrations block (after line 162), add:

```python
        # One-shot remap: legacy team default sentinel → new canonical name.
        # Narrow WHERE means subsequent runs are no-ops.
        try:
            self._conn.execute(
                "UPDATE tasks SET team='engineering' WHERE team='product_engineering'"
            )
            self._conn.commit()
        except sqlite3.OperationalError:
            pass
```

Update `insert_task` to drop `type` from the SQL. Find the INSERT statement (search for `INSERT INTO tasks`) and remove the `type` column and its `:type` placeholder. Likewise update `_row_to_task` (or wherever rows are materialized into `TaskRecord`) to stop reading `type`.

- [ ] **Step 5: Delete cross-audit stub**

Edit `src/infrastructure/audit_logger.py`: delete `log_cross_audit_stub` (around line 111) entirely. Grep for its call sites and delete them too:

```bash
grep -rn "log_cross_audit_stub" src/ tests/
```

- [ ] **Step 6: Update `SubmitTask` + routing**

Edit `src/daemon/routes/tasks.py`:

- Remove `TaskType` from the import at line 18.
- Replace the `SubmitTask` model (around line 29-31):

```python
class SubmitTask(BaseModel):
    team: str | None = None
    brief: str
```

- In the POST handler (around line 48), validate the team and assign the manager:

```python
@router.post("/tasks")
def submit_task(body: SubmitTask, request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_active(state)
    registry = state.teams
    team = body.team or "engineering"
    if team not in registry.teams():
        raise HTTPException(
            status_code=400,
            detail={"code": "unknown_team", "valid": registry.teams()},
        )
    manager = registry.manager_for_team(team).name
    task_id = _next_task_id(state.db)
    record = TaskRecord(
        id=task_id, brief=body.brief, team=team, assigned_agent=manager,
    )
    state.db.insert_task(record)
    state.queue.enqueue(task_id)
    return {"id": task_id, "team": team, "assigned_agent": manager}
```

(Keep the existing response-shape conventions — if your codebase uses a `TaskResponse` model, populate it instead.)

- [ ] **Step 7: Derive KB topic from team instead of task_type**

Edit `src/daemon/routes/kb.py`:

- Remove `from src.models import TaskType` (line 22) and the `_TOPIC_FOR_TASK_TYPE` dict (around line 202-206).
- Wherever the dict was used (around line 249), replace with:

```python
topic = task.team  # "engineering" | "content" | ... — directly usable as a topic tag
```

- [ ] **Step 8: Swap `--task` → `--team` in CLI**

Edit `src/cli.py` `cmd_run` (search for `--task`):

- Remove the `--task` argparse argument.
- Add `--team` argument, optional, default `None`. Pass it as `team` in the POST body.

- [ ] **Step 9: Update existing tests that pass `type=`**

Run:

```bash
grep -rln "TaskType\." tests/
```

Each hit: replace `type=TaskType.X` with `team="engineering"` (or the appropriate team) in `TaskRecord(...)` constructors; replace `TaskType.X` literal usages with string `"engineering"` / `"content"` as the call site demands. If a test is specifically exercising the old `type` behavior (e.g., topic derivation), rewrite it to exercise `team` behavior.

- [ ] **Step 10: Run the full unit suite**

Run: `uv run pytest tests/ -q`
Expected: all pass. Failures here are callers you missed — fix them in place, no scope expansion.

- [ ] **Step 11: Commit**

```bash
git add src/models.py src/infrastructure/database.py src/infrastructure/audit_logger.py src/daemon/routes/tasks.py src/daemon/routes/kb.py src/cli.py tests/
git commit -m "refactor: retire TaskType; replace with task.team for routing + KB topic"
```

---

## Task 5: Generalize `run_step` via registry lookups

**Files:**
- Modify: `src/orchestrator/run_step.py:122, 197, 209, 241, 371-378` — 5 hardcoded sites.
- Modify: `src/orchestrator/capabilities.py` — scope prompt to calling manager's team.
- Create: `tests/orchestrator/test_run_step_cross_team_rejection.py`
- Create: `tests/orchestrator/test_run_step_content_team.py` (placeholder; filled in Task 9).

- [ ] **Step 1: Write failing test for cross-team rejection**

Create `tests/orchestrator/test_run_step_cross_team_rejection.py`:

```python
from __future__ import annotations

from unittest.mock import MagicMock

from src.models import NextStep, TaskRecord
from src.orchestrator.teams import TeamsRegistry, DEFAULT_LAYOUT


def test_cross_team_delegation_is_rejected() -> None:
    registry = TeamsRegistry._from_layout(DEFAULT_LAYOUT)
    # Content Manager tries to delegate to dev_agent (wrong team).
    decision = NextStep(action="delegate", agent="dev_agent", prompt="x")
    caller_team = registry.team_for_manager("content_manager")
    target_team = registry.team_for_agent(decision.agent)
    assert caller_team == "content"
    assert target_team == "engineering"
    # The orchestrator logic under test should classify this mismatch as a
    # feedback step, not honor the delegation. Matching the invariant in spec
    # §"Cross-team delegation".
    assert caller_team != target_team
```

This is a small guard test that asserts the invariant the real orchestrator must enforce. The real delegation logic lives in `run_step.py`; we'll call through it after wiring.

- [ ] **Step 2: Rewire the 5 sites in `run_step.py`**

Before rewiring the sites, thread the registry into the Orchestrator facade. Edit `src/orchestrator/orchestrator.py`:

```python
# __init__ signature
def __init__(
    self, db: Database, settings: Settings, runtime: RuntimeDir,
    teams: TeamsRegistry,
) -> None:
    self._db = db
    self._settings = settings
    self._runtime = runtime
    self._teams = teams
    ...
```

Add a public accessor:

```python
@property
def teams(self) -> TeamsRegistry:
    return self._teams
```

Then update every construction site (daemon lifespan + test fixtures) to pass `teams`. Grep: `grep -rn "Orchestrator(" src/ tests/`. For daemon construction, `state.teams` is already populated (Task 2). For tests, pass `TeamsRegistry._from_layout(DEFAULT_LAYOUT)` from `src.orchestrator.teams`.

Now for each site, use `orch.teams`:

Site 1 (`run_step.py:122`, decision-parsing gate):

```python
# before
if agent == "engineering_head":
# after
if orch.teams.is_team_manager(agent):
```

Site 2 (`_default_agent_for_root`, line ~197): takes the task, returns `orch.teams.manager_for_team(task.team).name`.

Site 3 (capabilities injection skip, line ~209):

```python
# before
if agent != "engineering_head":
# after
if not orch.teams.is_team_manager(agent):
```

Site 4 (candidate-list exclusion, line ~241):

```python
# before: exclude only the EH workspace
# after: exclude the calling manager's workspace
if d.name != agent:
```

Site 5 (verdict reviewer, line ~371-378):

```python
# before
log_review_verdict(reviewer="engineering_head", ...)
# after — the reviewer is the manager of the parent task's team.
parent_team = parent.team
reviewer = orch.teams.manager_for_team(parent_team).name
log_review_verdict(reviewer=reviewer, ...)
```

Add cross-team enforcement right after the decision is parsed as `delegate`:

```python
if decision.action == "delegate":
    caller_team = orch.teams.team_for_manager(agent)
    target_team = orch.teams.team_for_agent(decision.agent)
    if caller_team is None or target_team is None or caller_team != target_team:
        # Feed an error back into the next step; count against runaway guard.
        feedback = (
            f"Invalid delegation: you are on team {caller_team!r}, "
            f"but {decision.agent!r} is on team {target_team!r}. "
            "Pick a worker on your own team, or escalate."
        )
        _record_feedback_step(orch, task, feedback)
        return
```

(`_record_feedback_step` is a thin helper we'll add next to the other `_record_*` helpers in the same file — pattern after how failed-decision feedback is already handled; the existing code has precedent for writing a `task_results` row + incrementing `orchestration_step_count`.)

- [ ] **Step 3: Scope the capabilities prompt**

Edit `src/orchestrator/capabilities.py`:

Currently `build_capabilities_prompt(agents, ...)` lists every known agent. Change its signature to take a `TeamsRegistry` and a `calling_manager: str`, and list only the workers on the calling manager's team plus their tiers. Pseudo-code:

```python
def build_capabilities_prompt(
    registry: TeamsRegistry,
    calling_manager: str,
    tiers: dict[str, PerformanceTier],
) -> str:
    team = registry.team_for_manager(calling_manager)
    if team is None:
        return ""
    manager = registry.manager_for_team(team)
    lines = [f"You are {calling_manager}. Your team is {team!r}."]
    lines.append("Your workers (with current performance tier):")
    for w in manager.workers:
        lines.append(f"- {w} (tier: {tiers.get(w, 'green')})")
    return "\n".join(lines)
```

Update the single caller in `run_step.py` accordingly.

- [ ] **Step 4: Run orchestrator tests**

Run: `uv run pytest tests/orchestrator/ tests/test_run_step*.py -v`
Expected: the cross-team guard test passes; existing run_step tests may fail if they relied on literal `"engineering_head"` or on `build_capabilities_prompt` with the old signature — fix those call sites.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest tests/ -q`
Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add src/orchestrator/run_step.py src/orchestrator/capabilities.py tests/orchestrator/
git commit -m "feat(orchestrator): generalize run_step for any team manager via TeamsRegistry"
```

---

## Task 6: KB delete — team-manager scope

**Files:**
- Modify: `src/daemon/routes/kb.py:320` — gate on `registry.is_team_manager(agent)`.
- Modify: `protocol/06-knowledge-base.md` — delete authority text.
- Create: `tests/daemon/test_kb_delete_team_managers.py`

- [ ] **Step 1: Write failing test**

Create `tests/daemon/test_kb_delete_team_managers.py`. Pattern-match the existing KB route test fixtures:

```python
from __future__ import annotations

from fastapi.testclient import TestClient

from tests.daemon.conftest import client  # type: ignore  # noqa: F401


def _seed_entry(client: TestClient, slug: str = "test-entry") -> None:
    # Add via the existing add endpoint so we don't bypass the store.
    client.post(
        "/kb",
        json={"agent": "engineering_head", "payload": _minimal_md(slug)},
    )


def _minimal_md(slug: str) -> str:
    return f"---\nslug: {slug}\ntitle: T\ntype: reference\ntopic: misc\n---\nbody"


def test_engineering_head_can_delete(client: TestClient) -> None:
    _seed_entry(client, "a")
    resp = client.request(
        "DELETE", "/kb/a",
        json={"agent": "engineering_head", "confirm": True, "rationale": "test"},
    )
    assert resp.status_code == 200


def test_content_manager_can_delete(client: TestClient) -> None:
    _seed_entry(client, "b")
    resp = client.request(
        "DELETE", "/kb/b",
        json={"agent": "content_manager", "confirm": True, "rationale": "test"},
    )
    assert resp.status_code == 200


def test_worker_is_rejected(client: TestClient) -> None:
    _seed_entry(client, "c")
    resp = client.request(
        "DELETE", "/kb/c",
        json={"agent": "dev_agent", "confirm": True, "rationale": "test"},
    )
    assert resp.status_code == 403


def test_founder_override_always_allowed(client: TestClient) -> None:
    _seed_entry(client, "d")
    resp = client.request(
        "DELETE", "/kb/d",
        json={"agent": "dev_agent", "as_founder": True, "confirm": True, "rationale": "founder"},
    )
    assert resp.status_code == 200
```

(Adjust `_minimal_md` and the DELETE-body shape to whatever the real route expects — inspect `src/daemon/routes/kb.py` for the exact Pydantic model.)

- [ ] **Step 2: Run — expect failure**

Run: `uv run pytest tests/daemon/test_kb_delete_team_managers.py -v`
Expected: the Content Manager test 403s (current gate is literal `agent != "engineering_head"`).

- [ ] **Step 3: Swap the gate**

Edit `src/daemon/routes/kb.py:320`:

```python
# before
if not as_founder and agent != "engineering_head":
# after
if not as_founder and not state.teams.is_team_manager(agent):
```

- [ ] **Step 4: Run — expect pass**

Run: `uv run pytest tests/daemon/test_kb_delete_team_managers.py -v`
Expected: all 4 tests pass.

- [ ] **Step 5: Update protocol**

Edit `protocol/06-knowledge-base.md`: find every mention of "Delete: engineering_head only" (or variants). Replace with:

> "Delete: any team manager; founder via `--as-founder`. The audit log captures the calling agent so cross-team deletions remain traceable."

- [ ] **Step 6: Update CLAUDE.md boilerplate**

Edit `CLAUDE.md` — in the "Knowledge Base" section, change "only Engineering Head deletes" to "any team manager deletes (audited); founder overrides via `--as-founder`".

Also update the `docstring` comment-string at `src/orchestrator/workspace_adapters.py` that says `"Delete: engineering_head only"` — change to `"Delete: any team manager"`.

- [ ] **Step 7: Commit**

```bash
git add src/daemon/routes/kb.py protocol/06-knowledge-base.md CLAUDE.md src/orchestrator/workspace_adapters.py tests/daemon/test_kb_delete_team_managers.py
git commit -m "feat(kb): team managers can delete entries (audit captures caller)"
```

---

## Task 7: `manage-agent` — team-manager scope + enrollment team assignment

**Files:**
- Modify: `src/daemon/routes/agents.py:98-140, 287-...` — rename + generalize helper; enroll into calling-manager's team.
- Create: `tests/daemon/test_manage_agent_team_scoping.py`

- [ ] **Step 1: Write failing test**

Create `tests/daemon/test_manage_agent_team_scoping.py`:

```python
from __future__ import annotations

from fastapi.testclient import TestClient

from tests.daemon.conftest import client, open_talk_for  # type: ignore  # noqa: F401


def test_content_manager_can_enroll_into_content(client: TestClient) -> None:
    talk_id = open_talk_for(client, "content_manager")
    resp = client.post("/agents/manage", json={
        "action": "enroll",
        "name": "seo_agent",
        "talk_id": talk_id,
        "description": "d", "system_prompt": "s", "repos": {},
    })
    assert resp.status_code == 200, resp.text
    # After enrollment, seo_agent should be a worker on team 'content'.
    # Observable via /agents/enrollments OR via a fresh TeamsRegistry load.


def test_content_manager_cannot_enroll_into_engineering(client: TestClient) -> None:
    talk_id = open_talk_for(client, "content_manager")
    resp = client.post("/agents/manage", json={
        "action": "enroll",
        "name": "hostile_agent",
        "talk_id": talk_id,
        "target_team": "engineering",
        "description": "d", "system_prompt": "s", "repos": {},
    })
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "cross_team_forbidden"


def test_engineering_head_still_works(client: TestClient) -> None:
    talk_id = open_talk_for(client, "engineering_head")
    resp = client.post("/agents/manage", json={
        "action": "enroll",
        "name": "codex_dev",
        "talk_id": talk_id,
        "description": "d", "system_prompt": "s", "repos": {}, "executor": "codex",
    })
    assert resp.status_code == 200, resp.text
```

`open_talk_for` is a helper we expect in `tests/daemon/conftest.py`; add it if missing (opens a talk via `POST /talks/start` with the given agent name and returns the talk id).

- [ ] **Step 2: Run — expect failure**

Run: `uv run pytest tests/daemon/test_manage_agent_team_scoping.py -v`
Expected: content-manager tests 403 unconditionally because `_require_eh_auth` hardcodes `"engineering_head"`.

- [ ] **Step 3: Rename helper; generalize**

In `src/daemon/routes/agents.py`, replace `_require_eh_auth` (lines 98-140) with `_require_team_manager_auth`:

```python
def _require_team_manager_auth(body: ManageAgentBody, state: DaemonState) -> tuple[str, str]:
    """Authorize a manage-agent call as a team manager. Returns
    ``(manager_name, manager_team)``.
    """
    if body.talk_id is not None:
        talk = state.db.get_talk(body.talk_id)
        if talk is None:
            raise HTTPException(404, detail=f"talk {body.talk_id!r} not found")
        if not state.teams.is_team_manager(talk.agent_name):
            raise HTTPException(403, detail="manage-agent requires a team-manager talk")
        if talk.status != TalkStatus.OPEN:
            raise HTTPException(
                403, detail=f"talk {body.talk_id!r} is {talk.status.value}, not open",
            )
        manager_name = talk.agent_name
    else:
        # Task path: find which team manager owns this session.
        manager_name = None
        for candidate in state.teams.all_agents():
            if not state.teams.is_team_manager(candidate):
                continue
            expected = state.sessions.get_active(body.task_id, candidate)
            if expected is not None and expected == body.session_id:
                manager_name = candidate
                break
        if manager_name is None:
            raise HTTPException(
                403, detail="manage-agent requires an active team-manager session",
            )

    team = state.teams.team_for_manager(manager_name)
    assert team is not None  # is_team_manager() above established this
    return manager_name, team
```

- [ ] **Step 4: Use returned team in enrollment flow**

In the `manage_agent` handler (around line 287), replace the `_require_eh_auth(body, state)` call with:

```python
manager_name, manager_team = _require_team_manager_auth(body, state)
```

After a successful `enroll`, update the registry under the lock:

```python
async with state.teams_lock:
    registry = state.teams
    target_team = body.target_team or manager_team
    if target_team != manager_team:
        raise HTTPException(
            403, detail={"code": "cross_team_forbidden",
                         "caller_team": manager_team, "requested_team": target_team},
        )
    registry.add_worker(manager_team, body.name)
    registry.save(state.runtime)
```

For `terminate`:

```python
async with state.teams_lock:
    if state.teams.team_for_agent(body.name) != manager_team:
        raise HTTPException(
            403, detail={"code": "cross_team_forbidden"},
        )
    state.teams.remove_worker(manager_team, body.name)
    state.teams.save(state.runtime)
```

Add `target_team: str | None = None` to `ManageAgentBody`. Update `protocol/skills/manage-agent/SKILL.md` to document the optional field (defaults to caller's team).

- [ ] **Step 5: Run — expect pass**

Run: `uv run pytest tests/daemon/test_manage_agent_team_scoping.py -v`
Expected: all 3 pass.

- [ ] **Step 6: Update `GET /agents/enrollments`**

In `list_enrollments` (same file), filter by `team = :calling_manager_team` when the request authenticates as a team manager, keeping the existing founder-visible code path. Pattern-match how existing filtering is done.

- [ ] **Step 7: Commit**

```bash
git add src/daemon/routes/agents.py protocol/skills/manage-agent/SKILL.md tests/daemon/test_manage_agent_team_scoping.py
git commit -m "feat(agents): generalize manage-agent to any team manager with cross-team guard"
```

---

## Task 8: Content Team bootstrap via `opc init-agent`

**Files:**
- Modify: `src/orchestrator/prompt_loader.py` — `_AGENT_SOURCES` gains `content_manager` + `content_writer`.
- Modify: `src/daemon/routes/agents.py:init_agents` — iterate `registry.all_agents()` instead of just-existing-workspace names when no explicit agent filter.

- [ ] **Step 1: Register new prompts**

Edit `src/orchestrator/prompt_loader.py:9-16`:

```python
_AGENT_SOURCES: dict[str, tuple[str, str]] = {
    "engineering_head": ("02-system-prompts-managers.md", "Engineering Head"),
    "content_manager": ("02-system-prompts-managers.md", "Content Manager"),
    "product_manager": ("03-system-prompts-workers.md", "Product Manager"),
    "dev_agent": ("03-system-prompts-workers.md", "Dev Agent"),
    "payment_agent": ("03-system-prompts-workers.md", "Payment Agent"),
    "qa_engineer": ("03-system-prompts-workers.md", "QA Engineer"),
    "content_writer": ("03-system-prompts-workers.md", "Content Writer"),
    "content_qa": ("03-system-prompts-workers.md", "Content QA"),
}
```

- [ ] **Step 2: Write a failing test for init-agent picking up Content Team**

Add to `tests/test_prompt_loader.py` (or create one if it doesn't exist):

```python
from pathlib import Path

from src.config import Settings
from src.orchestrator.prompt_loader import load_system_prompt


def test_content_manager_prompt_loads() -> None:
    s = Settings()
    prompt = load_system_prompt(s.get_protocol_dir(), "content_manager")
    assert prompt.startswith("You are the Content Manager")


def test_content_writer_prompt_loads() -> None:
    s = Settings()
    prompt = load_system_prompt(s.get_protocol_dir(), "content_writer")
    assert "Content Writer" in prompt or "You are the Content Writer" in prompt
```

Run: `uv run pytest tests/test_prompt_loader.py -v`
Expected: pass (since the `_AGENT_SOURCES` change + the existing protocol markdown are now in sync).

- [ ] **Step 3: Generalize init-agent target enumeration**

Edit `src/daemon/routes/agents.py:init_agents`:

When `body.agent is None`, build the target set from:
1. `state.teams.all_agents()` (built-ins declared in teams.yaml), PLUS
2. Any approved enrollment in the `agent_enrollments` table, PLUS
3. Any existing `workspaces/<name>/` directory (backward compatible — legacy manually-scaffolded dirs keep bootstrapping).

Concretely, replace:

```python
ws_dir = state.runtime.workspaces_dir
targets = sorted(d.name for d in ws_dir.iterdir() if d.is_dir()) if ws_dir.exists() else []
```

with:

```python
ws_dir = state.runtime.workspaces_dir
known: set[str] = set(state.teams.all_agents())
if ws_dir.exists():
    known.update(d.name for d in ws_dir.iterdir() if d.is_dir())
# Approved enrollments are a DB concern — fetch via an existing helper.
known.update(state.db.list_approved_agent_names())  # add this helper if absent
targets = sorted(known)
```

If `list_approved_agent_names` doesn't exist, add the trivial wrapper:

```python
@_synchronized
def list_approved_agent_names(self) -> list[str]:
    cur = self._conn.execute(
        "SELECT name FROM agent_enrollments WHERE status='approved'"
    )
    return [r["name"] for r in cur.fetchall()]
```

- [ ] **Step 4: Smoke-test end-to-end with real runtime**

```bash
# In a disposable tmp runtime:
TMP=$(mktemp -d)
uv run opc init "$TMP"
uv run opc init-agent content_manager
ls "$TMP/workspaces/content_manager/"
```

Expected: the workspace has `CLAUDE.md`, `.claude/settings.json`, `learnings.md`, `scorecard.md`, `task_history.md`. The CLAUDE.md first section should contain "You are the Content Manager".

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/prompt_loader.py src/daemon/routes/agents.py src/infrastructure/database.py tests/test_prompt_loader.py
git commit -m "feat(agents): init-agent enumerates full TeamsRegistry (Content Team bootstrap)"
```

---

## Task 9: Content Team MVP flow tests (unit + integration)

**Files:**
- Create: `tests/orchestrator/test_run_step_content_team.py`
- Create: `tests/integration/test_content_team_e2e.py`

- [ ] **Step 1: Add the run_step unit tests (scripted fake executor)**

Create `tests/orchestrator/test_run_step_content_team.py`. Pattern-match an existing scripted fake-executor test in `tests/orchestrator/test_run_step_*.py` — the core idea is a `FakeExecutor` that returns pre-queued completion payloads in order.

```python
from __future__ import annotations

import pytest

from src.models import NextStep, TaskRecord, TaskStatus

# Reuse the existing fixtures that build an Orchestrator with a fake executor.
# If the pattern is not already available as a pytest fixture, extract it
# from an existing run_step test into tests/orchestrator/conftest.py.


def _submit_content_task(orch, brief: str = "Write Macau visa guide") -> str:
    task = TaskRecord(id="TASK-C1", brief=brief, team="content", assigned_agent="content_manager")
    orch.db.insert_task(task)
    return task.id


def test_pass_path_completes_task(orch_with_fake_executor):
    orch, fake = orch_with_fake_executor
    tid = _submit_content_task(orch)
    # CM step 1: delegate to content_writer.
    fake.enqueue("content_manager", decision=NextStep(
        action="delegate", agent="content_writer", prompt="write the guide",
    ), summary="delegating to writer")
    # Writer step: produces artifact, completes.
    fake.enqueue("content_writer", summary="draft.md written", artifact_dir=f"artifacts/{tid}")
    # CM step 2: delegate to content_qa.
    fake.enqueue("content_manager", decision=NextStep(
        action="delegate", agent="content_qa", prompt="review",
    ), summary="delegating to QA")
    # QA step: PASS.
    fake.enqueue("content_qa", summary="VERDICT: PASS — draft is accurate.")
    # CM step 3: done.
    fake.enqueue("content_manager", decision=NextStep(
        action="done", summary="published",
    ), summary="content approved")

    orch.run_task_to_completion(tid, max_steps=10)
    assert orch.db.get_task(tid).status == TaskStatus.COMPLETED


def test_revise_path_bumps_revision_count(orch_with_fake_executor):
    orch, fake = orch_with_fake_executor
    tid = _submit_content_task(orch)
    fake.enqueue("content_manager", decision=NextStep(
        action="delegate", agent="content_writer", prompt="write",
    ))
    fake.enqueue("content_writer", summary="v1 draft")
    fake.enqueue("content_manager", decision=NextStep(
        action="delegate", agent="content_qa", prompt="review",
    ))
    fake.enqueue("content_qa", summary="VERDICT: REVISE — section 3 unclear.")
    fake.enqueue("content_manager", decision=NextStep(
        action="delegate", agent="content_writer", prompt="revise section 3",
    ))
    fake.enqueue("content_writer", summary="v2 draft")
    fake.enqueue("content_manager", decision=NextStep(
        action="delegate", agent="content_qa", prompt="review v2",
    ))
    fake.enqueue("content_qa", summary="VERDICT: PASS")
    fake.enqueue("content_manager", decision=NextStep(action="done", summary="ok"))

    orch.run_task_to_completion(tid, max_steps=10)
    task = orch.db.get_task(tid)
    assert task.status == TaskStatus.COMPLETED
    assert task.revision_count >= 1


def test_reject_path_escalates(orch_with_fake_executor):
    orch, fake = orch_with_fake_executor
    tid = _submit_content_task(orch)
    fake.enqueue("content_manager", decision=NextStep(
        action="delegate", agent="content_writer", prompt="write",
    ))
    fake.enqueue("content_writer", summary="draft")
    fake.enqueue("content_manager", decision=NextStep(
        action="delegate", agent="content_qa", prompt="review",
    ))
    fake.enqueue("content_qa", summary="VERDICT: REJECT — politically sensitive.")
    fake.enqueue("content_manager", decision=NextStep(
        action="escalate", reason="politically sensitive", summary="needs founder",
    ))
    orch.run_task_to_completion(tid, max_steps=10)
    task = orch.db.get_task(tid)
    assert task.status == TaskStatus.BLOCKED
    assert task.block_kind is not None and task.block_kind.value == "escalated"
```

`orch_with_fake_executor` is the shared fixture. If it doesn't exist yet, extract it from one of the existing `tests/orchestrator/test_run_step_*.py` files into `tests/orchestrator/conftest.py` — the pattern is: build an in-memory `Database`, a temp `RuntimeDir`, an `Orchestrator` with a `FakeExecutor` that pops `enqueue`d completion payloads.

Driver helper (put in `tests/orchestrator/conftest.py` alongside the fixture — `Orchestrator` has no built-in run-to-completion; the daemon does that via the queue):

```python
from src.models import TaskStatus

def run_task_to_completion(orch, task_id: str, max_steps: int = 10) -> None:
    for _ in range(max_steps):
        task = orch._db.get_task(task_id)
        if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            return
        if task.status == TaskStatus.BLOCKED:
            return
        orch.run_step(task_id)
    raise AssertionError(f"task {task_id} did not terminate within {max_steps} steps")
```

Update the test bodies above to call `run_task_to_completion(orch, tid)` instead of `orch.run_task_to_completion(tid, max_steps=10)`.

- [ ] **Step 2: Run — expect pass (mostly)**

Run: `uv run pytest tests/orchestrator/test_run_step_content_team.py -v`
Expected: PASS path passes. REVISE/REJECT may require small additions to the orchestrator — specifically:

- Revision-count bump condition: the spec says "bumps it when `decision.agent == previous_delegate_target_after_qa`". Confirm the existing EH path already does this for `dev_agent`; generalize to any post-QA redelegation.
- Escalation path: no changes expected — `{"action": "escalate"}` is already supported in the NextStep parser.

If a test is red, fix the orchestrator logic — not the test.

- [ ] **Step 3: Write the integration test**

Create `tests/integration/test_content_team_e2e.py` modeled on the existing `tests/integration/test_*.py` fake-Claude pattern. Skim `tests/integration/` for the fake-Claude binary harness. The integration test:

1. Spawns a real daemon against a temp runtime.
2. Runs `opc init-agent` to bootstrap `content_manager`, `content_writer`, `content_qa`.
3. Seeds the fake-Claude binary with three scripts (one per agent) that produce the PASS path.
4. `POST /tasks` with `team=content`.
5. Polls task state until COMPLETED or timeout (60s).
6. Asserts: final status COMPLETED; an artifact file exists under `workspaces/content_writer/artifacts/<task_id>/draft.md`; `opc recall TASK-xxx --fetch-artifact draft.md` returns the file contents.

```python
import pytest

pytestmark = pytest.mark.integration

# Replicate the daemon+fakeclaude harness from the nearest existing
# integration test — copy the fixture, don't reinvent it. The test body
# then is a straight POST /tasks → poll → assertions sequence.
```

- [ ] **Step 4: Run integration**

Run: `uv run pytest tests/integration/test_content_team_e2e.py -v -m integration`
Expected: pass within 60s. If it hangs, check that the fake-Claude script for `content_manager` is correctly issuing `opc report-completion --from-file` with a NextStep decision.

- [ ] **Step 5: Commit**

```bash
git add tests/orchestrator/test_run_step_content_team.py tests/integration/test_content_team_e2e.py tests/orchestrator/conftest.py
git commit -m "test: Content Team MVP flow (unit + integration) — PASS/REVISE/REJECT paths"
```

---

## Task 10: Docs sync

**Files:**
- Modify: `CLAUDE.md` — drop task_type examples; swap `--task` → `--team`; update KB delete / manage-agent authority lines.
- Modify: `README.md` — `opc run` examples use `--team`; remove any TaskType-derived language.
- Modify: `protocol/05a-teams.md` (if it references EH-only manage-agent) — generalize.

- [ ] **Step 1: Grep for stale references**

```bash
grep -rn "task_type\|--task \|TaskType\|engineering_head only\|EH-only" CLAUDE.md README.md protocol/
```

Expected: a punch list of stale references. For each:

- `--task engineering` → `--team engineering` (CLI example).
- `task_type` in any narrative → reword to `task.team`.
- "only Engineering Head deletes" → "any team manager deletes (audited)".
- "EH-only" in manage-agent docs → "any team manager (team-scoped)".

- [ ] **Step 2: Apply edits**

Use Edit / grep-and-edit to fix each hit from Step 1. Keep the changes minimal — don't rewrite sections that happen to sit near a stale reference.

In CLAUDE.md specifically:
- The `opc run` example line becomes: `opc run --team engineering --brief "Add Alipay support"`.
- Remove the `opc run --task implement_feature --brief ...` example.
- In "Knowledge Base" paragraph: replace "only Engineering Head deletes" → "any team manager deletes (audited); founder overrides via `--as-founder`".

- [ ] **Step 3: Confirm docs are coherent**

```bash
grep -rn "task_type\|--task \|TaskType\|engineering_head only\|EH-only" CLAUDE.md README.md protocol/
```

Expected: no hits (or only in `docs/superpowers/specs/` / `docs/superpowers/plans/` — those are historical artifacts and should not be edited).

- [ ] **Step 4: Full-suite green check**

Run: `uv run pytest tests/ -q` and `uv run pytest tests/ -q -m integration`
Expected: both green.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md README.md protocol/
git commit -m "docs: sync CLAUDE.md/README/protocol to TeamsRegistry + --team vocabulary"
```

---

## Final step: open the PR

- [ ] **Run**

```bash
gh pr create --title "feat(content-team): thin-slice Content Team + team abstraction" --body "$(cat <<'EOF'
## Summary

- New TeamsRegistry abstraction (`src/orchestrator/teams.py`) backed by `<runtime>/teams.yaml`.
- Generalizes orchestrator, KB-delete, and manage-agent from EH-only to any team manager.
- Retires `TaskType`; replaces routing with an explicit `--team` flag on `opc run`.
- Moves `gh` allow-rule prefixes from a Python constant into `### Allow Rules` protocol subsections.
- Bootstraps Content Manager + Content Writer + Content QA. Full unit + integration coverage.

## Test plan

- [ ] `uv run pytest tests/ -q`
- [ ] `uv run pytest tests/ -q -m integration`
- [ ] End-to-end: `opc init $(mktemp -d)`, `opc init-agent`, `opc run --team content --brief "Write Macau visa guide"`, observe PASS path.
EOF
)"
```

---

## Cross-cutting notes

- **`TaskRecord.team` default:** this plan changes it from `"product_engineering"` to `"engineering"` (in Pydantic, in the DDL, and via a one-shot UPDATE). The pre-existing value was a dead sentinel with no readers — changing it is safe.
- **SQLite `type` column stays:** `CREATE TABLE IF NOT EXISTS` won't drop it on existing runtimes. That's fine — nothing reads it after Task 4. A follow-up migration can table-rebuild later.
- **Registry persistence lock:** every `registry.save(runtime)` must be inside `async with state.teams_lock` at the caller; the registry itself doesn't lock because it's a value object.
- **Cross-team same-team check:** both `team_for_manager` and `team_for_agent` return `str | None`. A cross-team guard must treat `None == None` as NOT a match (explicitly written in the guard). The spec §"Cross-team delegation" is the authority here.
- **Don't widen scope:** e.g. don't migrate CX or Ops managers yet. This plan is the Content Team thin-slice plus the abstractions it needs; nothing more.
