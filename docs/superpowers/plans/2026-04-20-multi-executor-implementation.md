# Multi-Executor Agent Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-agent executor selection so some agents can run on Claude while others run on Codex, while preserving backward compatibility and the existing `opc` callback contract.

**Architecture:** Introduce provider-aware executor resolution, provider-specific workspace adapters, and provider-specific subprocess launchers while keeping orchestration, task lifecycle, session tracking, and callback payloads shared. Reuse the current `protocol/skills/start-task` SOP as the canonical procedure source, but render it into Claude and Codex bootstrap surfaces separately.

**Tech Stack:** Python 3.13, FastAPI, Pydantic v2, pytest, git worktrees, Claude Code CLI, Codex CLI

---

## File Structure

### New files

- `src/orchestrator/executors.py`
  Provider-specific executor implementations plus shared resolver helpers.
- `src/orchestrator/workspace_adapters.py`
  Provider-specific workspace bootstrap/rendering logic.
- `tests/test_workspace_adapters.py`
  Provider-aware bootstrap tests for Claude and Codex workspaces.
- `tests/integration/fake_codex.sh`
  Fake Codex CLI used by integration tests.

### Modified files

- `src/config.py`
  Add Codex-specific CLI path and any executor-related defaults.
- `src/daemon/agent_config.py`
  Add `executor` support to `agent.yaml` load/write helpers.
- `src/daemon/routes/agents.py`
  Accept/store executor in init/enrollment/update/approve flows.
- `src/infrastructure/database.py`
  Persist optional executor on enrollments and expose it through CRUD helpers.
- `src/orchestrator/orchestrator.py`
  Resolve provider per agent, use provider-specific adapter/executor, and check provider-specific readiness markers.
- `src/orchestrator/context_builder.py`
  Either reduce to shared persistent-file helpers or delegate provider bootstrap to the new adapter module.
- `src/cli.py`
  Extend manage-agent payload parsing help text if executor is added to JSON file payloads.
- `tests/test_executor.py`
  Replace Claude-only command assertions with provider-specific tests.
- `tests/test_context_builder.py`
  Update or split tests so provider bootstrap is no longer Claude-only.
- `tests/test_agent_config.py`
  Cover default executor and explicit executor persistence.
- `tests/daemon/test_routes_agents.py`
  Cover executor through enrollment, approval, and workspace bootstrap.
- `tests/test_database.py`
  Cover enrollment executor persistence/defaulting behavior.
- `tests/test_orchestrator.py`
  Cover provider resolution, readiness marker handling, and prompt selection.
- `tests/integration/conftest.py`
  Add fixtures for fake Codex.
- `tests/integration/test_end_to_end.py`
  Add a Codex-configured path and mixed-fleet path.
- `CLAUDE.md`
  Update architecture and runtime docs for mixed executors.
- `README.md`
  Update user-facing setup and configuration docs.
- `protocol/05a-teams.md`
  Update agent runtime mapping language.
- `protocol/05b-agent-runtime.md`
  Update runtime model from future abstraction to implemented mixed-provider model.
- `protocol/05c-orchestrator.md`
  Update orchestrator/provider docs.

## Task 1: Add Per-Agent Executor Configuration

**Files:**
- Modify: `src/daemon/agent_config.py`
- Modify: `src/infrastructure/database.py`
- Modify: `src/daemon/routes/agents.py`
- Modify: `src/cli.py`
- Test: `tests/test_agent_config.py`
- Test: `tests/test_database.py`
- Test: `tests/daemon/test_routes_agents.py`

- [ ] **Step 1: Write the failing agent config tests**

```python
from pathlib import Path

from src.daemon.agent_config import load_agent_config, write_default_agent_config


def test_write_default_agent_config_defaults_executor_to_claude(tmp_path: Path) -> None:
    write_default_agent_config(tmp_path)
    cfg = load_agent_config(tmp_path)
    assert cfg["executor"] == "claude"
    assert cfg["repos"] == {}


def test_load_agent_config_preserves_explicit_codex_executor(tmp_path: Path) -> None:
    (tmp_path / "agent.yaml").write_text("executor: codex\nrepos: {}\n")
    cfg = load_agent_config(tmp_path)
    assert cfg["executor"] == "codex"
```

- [ ] **Step 2: Run the focused agent config tests to verify they fail**

Run: `uv run pytest tests/test_agent_config.py -q`
Expected: FAIL because `write_default_agent_config()` does not write an `executor` key yet.

- [ ] **Step 3: Implement minimal `agent.yaml` executor support**

```python
def load_agent_config(workspace: Path) -> dict:
    path = workspace / "agent.yaml"
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    data.setdefault("executor", "claude")
    data.setdefault("repos", {})
    return data


def write_default_agent_config(workspace: Path) -> None:
    path = workspace / "agent.yaml"
    if path.exists():
        return
    workspace.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump({"executor": "claude", "repos": {}}, default_flow_style=False))
```

- [ ] **Step 4: Run the focused agent config tests to verify they pass**

Run: `uv run pytest tests/test_agent_config.py -q`
Expected: PASS

- [ ] **Step 5: Write the failing enrollment/database tests**

```python
def test_insert_enrollment_persists_executor(db):
    db.insert_enrollment("codex_agent", "desc", "prompt", executor="codex")
    e = db.get_enrollment("codex_agent")
    assert e["executor"] == "codex"


def test_insert_enrollment_defaults_executor_to_claude(db):
    db.insert_enrollment("default_agent", "desc", "prompt")
    e = db.get_enrollment("default_agent")
    assert e["executor"] == "claude"
```

- [ ] **Step 6: Run the database tests to verify they fail**

Run: `uv run pytest tests/test_database.py -q`
Expected: FAIL because `agent_enrollments` has no executor column or CRUD support yet.

- [ ] **Step 7: Add executor persistence to enrollment storage and routes**

```python
class ManageAgentBody(BaseModel):
    action: ManageAgentAction
    name: str
    task_id: str
    session_id: str
    description: str | None = None
    system_prompt: str | None = None
    repos: dict[str, str] | None = None
    executor: str | None = None
```

```python
state.db.insert_enrollment(
    name=body.name,
    description=body.description,
    system_prompt=body.system_prompt,
    repos=body.repos,
    executor=body.executor or "claude",
)
```

```python
def insert_enrollment(
    self,
    name: str,
    description: str,
    system_prompt: str,
    repos: dict[str, str] | None = None,
    executor: str = "claude",
) -> None:
    ...
```

- [ ] **Step 8: Run the agent config, database, and route tests**

Run: `uv run pytest tests/test_agent_config.py tests/test_database.py tests/daemon/test_routes_agents.py -q`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add src/daemon/agent_config.py src/infrastructure/database.py src/daemon/routes/agents.py src/cli.py tests/test_agent_config.py tests/test_database.py tests/daemon/test_routes_agents.py
git commit -m "feat(runtime): add per-agent executor configuration"
```

## Task 2: Introduce Provider-Specific Workspace Adapters

**Files:**
- Create: `src/orchestrator/workspace_adapters.py`
- Modify: `src/orchestrator/context_builder.py`
- Test: `tests/test_workspace_adapters.py`
- Test: `tests/test_context_builder.py`

- [ ] **Step 1: Write failing workspace adapter tests**

```python
def test_claude_adapter_writes_claude_workspace_files(tmp_path, test_settings):
    from src.orchestrator.workspace_adapters import ClaudeWorkspaceAdapter

    ws = tmp_path / "claude-agent"
    ClaudeWorkspaceAdapter(test_settings).prepare_workspace(ws, "dev_agent", "system prompt")

    assert (ws / "CLAUDE.md").exists()
    assert (ws / ".claude" / "settings.json").exists()
    assert (ws / ".claude" / "skills" / "start-task" / "SKILL.md").exists()


def test_codex_adapter_writes_agents_md_bootstrap(tmp_path, test_settings):
    from src.orchestrator.workspace_adapters import CodexWorkspaceAdapter

    ws = tmp_path / "codex-agent"
    CodexWorkspaceAdapter(test_settings).prepare_workspace(ws, "dev_agent", "system prompt")

    assert (ws / "AGENTS.md").exists()
    assert not (ws / ".claude" / "skills" / "start-task" / "SKILL.md").exists()
```

- [ ] **Step 2: Run the workspace adapter tests to verify they fail**

Run: `uv run pytest tests/test_workspace_adapters.py -q`
Expected: FAIL because `workspace_adapters.py` does not exist yet.

- [ ] **Step 3: Implement provider-specific workspace adapters**

```python
class ClaudeWorkspaceAdapter:
    def __init__(self, settings: Settings) -> None:
        self._builder = ContextBuilder(settings)

    def provider_name(self) -> str:
        return "claude"

    def readiness_marker(self, workspace: Path) -> Path:
        return workspace / ".claude" / "skills" / "start-task" / "SKILL.md"

    def prepare_workspace(self, workspace: Path, agent_name: str, system_prompt: str) -> None:
        self._builder.ensure_workspace_ready(workspace, agent_name, system_prompt)
```

```python
class CodexWorkspaceAdapter:
    def __init__(self, settings: Settings) -> None:
        self._builder = ContextBuilder(settings)

    def provider_name(self) -> str:
        return "codex"

    def readiness_marker(self, workspace: Path) -> Path:
        return workspace / "AGENTS.md"

    def prepare_workspace(self, workspace: Path, agent_name: str, system_prompt: str) -> None:
        self._builder.ensure_persistent_workspace_files(workspace, agent_name)
        self._builder.write_agents_md(workspace, agent_name, system_prompt)
```

- [ ] **Step 4: Refactor shared persistent file helpers out of provider-specific Claude bootstrap**

```python
def ensure_persistent_workspace_files(self, workspace: Path, agent_name: str) -> list[str]:
    workspace.mkdir(parents=True, exist_ok=True)
    ...
    return repo_names
```

- [ ] **Step 5: Run the adapter and context-builder tests**

Run: `uv run pytest tests/test_workspace_adapters.py tests/test_context_builder.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/orchestrator/workspace_adapters.py src/orchestrator/context_builder.py tests/test_workspace_adapters.py tests/test_context_builder.py
git commit -m "refactor(runtime): add provider-specific workspace adapters"
```

## Task 3: Introduce Provider-Specific Executors

**Files:**
- Create: `src/orchestrator/executors.py`
- Modify: `src/config.py`
- Modify: `tests/test_executor.py`

- [ ] **Step 1: Write failing executor tests for Claude and Codex**

```python
def test_claude_executor_builds_expected_command(tmp_path):
    ...
    assert cmd[:3] == ["claude", "-p", "prompt body"]
    assert "--permission-mode" in cmd


def test_codex_executor_builds_expected_command(tmp_path):
    ...
    assert cmd[:2] == ["codex", "exec"]
    assert "--sandbox" in cmd
    assert "workspace-write" in cmd
    assert "--skip-git-repo-check" in cmd
```

- [ ] **Step 2: Run the executor tests to verify they fail**

Run: `uv run pytest tests/test_executor.py -q`
Expected: FAIL because `src/orchestrator/executors.py` does not exist and the old tests target a single Claude-only class.

- [ ] **Step 3: Implement provider-specific executors and config settings**

```python
class ClaudeExecutor:
    def run(...):
        cmd = [
            self._cli_path,
            "-p", prompt,
            "--permission-mode", self._permission_mode,
            "--allowedTools", "Bash(opc *)",
        ]
```

```python
class CodexExecutor:
    def run(...):
        cmd = [
            self._cli_path,
            "exec",
            "--sandbox", self._sandbox_mode,
            "--skip-git-repo-check",
            "--json",
            "-",
        ]
        subprocess.run(cmd, input=prompt, ...)
```

```python
class Settings(BaseSettings):
    claude_cli_path: str = "claude"
    codex_cli_path: str = "codex"
    permission_mode: str = "auto"
    codex_sandbox_mode: str = "workspace-write"
```

- [ ] **Step 4: Run the executor tests to verify they pass**

Run: `uv run pytest tests/test_executor.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/executors.py src/config.py tests/test_executor.py
git commit -m "feat(runtime): add Claude and Codex executors"
```

## Task 4: Wire Provider Resolution into the Orchestrator

**Files:**
- Modify: `src/orchestrator/orchestrator.py`
- Modify: `src/daemon/routes/agents.py`
- Test: `tests/test_orchestrator.py`
- Test: `tests/daemon/test_run_step_integration.py`

- [ ] **Step 1: Write failing orchestrator tests for provider resolution**

```python
def test_run_agent_uses_codex_readiness_marker(tmp_path, orchestrator):
    ...
    (workspace / "AGENTS.md").write_text("# Agent bootstrap\n")
    ...
    assert result.success is True


def test_missing_executor_defaults_to_claude(tmp_path, orchestrator):
    ...
    assert selected_provider == "claude"
```

- [ ] **Step 2: Run the orchestrator tests to verify they fail**

Run: `uv run pytest tests/test_orchestrator.py tests/daemon/test_run_step_integration.py -q`
Expected: FAIL because the orchestrator always uses the Claude executor path and Claude readiness marker.

- [ ] **Step 3: Implement executor/adapter resolution in the orchestrator**

```python
def _resolve_executor_name(self, agent_name: str) -> str:
    workspace = self._runtime.workspaces_dir / agent_name
    cfg = load_agent_config(workspace)
    return cfg.get("executor") or "claude"
```

```python
provider = self._resolve_executor_name(agent_name)
adapter = get_workspace_adapter(provider, self._settings)
executor = get_executor(provider, self._settings)
skill_marker = adapter.readiness_marker(workspace)
```

- [ ] **Step 4: Update init/approve flows to prepare the correct provider workspace**

```python
cfg = load_agent_config(workspace)
provider = cfg.get("executor") or enrollment.get("executor") or "claude"
adapter = get_workspace_adapter(provider, state.settings)
await asyncio.to_thread(adapter.prepare_workspace, workspace, agent_name, sys_prompt)
```

- [ ] **Step 5: Run the orchestrator and agent-route tests**

Run: `uv run pytest tests/test_orchestrator.py tests/daemon/test_routes_agents.py tests/daemon/test_run_step_integration.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/orchestrator/orchestrator.py src/daemon/routes/agents.py tests/test_orchestrator.py tests/daemon/test_run_step_integration.py tests/daemon/test_routes_agents.py
git commit -m "refactor(orchestrator): resolve executors per agent"
```

## Task 5: Add Codex and Mixed-Fleet Integration Coverage

**Files:**
- Create: `tests/integration/fake_codex.sh`
- Modify: `tests/integration/conftest.py`
- Modify: `tests/integration/test_end_to_end.py`

- [ ] **Step 1: Write failing integration cases for Codex and mixed fleets**

```python
def test_end_to_end_with_codex_executor(...):
    ...
    assert task["status"] == "completed"


def test_mixed_fleet_can_delegate_between_claude_and_codex(...):
    ...
    assert child["assigned_agent"] == "dev_agent"
    assert child["status"] == "completed"
```

- [ ] **Step 2: Run the integration tests to verify they fail**

Run: `uv run pytest tests/integration/test_end_to_end.py -q`
Expected: FAIL because there is no fake Codex runner or Codex executor path.

- [ ] **Step 3: Implement the fake Codex runner**

```bash
#!/usr/bin/env bash
set -e

PROMPT=""
if [[ "${*: -1}" == "-" ]]; then
  PROMPT="$(cat)"
else
  PROMPT="${*: -1}"
fi

TASK_ID=$(echo "$PROMPT" | awk -F': ' '/^[[:space:]]*task_id: /{print $2; exit}')
SESSION_ID=$(echo "$PROMPT" | awk -F': ' '/^[[:space:]]*session_id: /{print $2; exit}')

if [[ -n "${FAKE_CODEX_PLAN:-}" && -f "$FAKE_CODEX_PLAN" ]]; then
  bash "$FAKE_CODEX_PLAN" "$TASK_ID" "$SESSION_ID"
fi
```

- [ ] **Step 4: Run the integration tests to verify they pass**

Run: `uv run pytest tests/integration/test_end_to_end.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/integration/fake_codex.sh tests/integration/conftest.py tests/integration/test_end_to_end.py
git commit -m "test(integration): cover Codex and mixed-fleet execution"
```

## Task 6: Update Documentation and Provider-Neutral SOP Wording

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: `protocol/05a-teams.md`
- Modify: `protocol/05b-agent-runtime.md`
- Modify: `protocol/05c-orchestrator.md`
- Modify: `protocol/skills/start-task/SKILL.md`
- Modify: `protocol/skills/manage-repo/SKILL.md`
- Modify: `protocol/skills/manage-agent/SKILL.md`
- Test: `tests/test_skills.py`

- [ ] **Step 1: Write failing documentation/skills assertions**

```python
def test_start_task_skill_documents_provider_neutral_from_file_rationale() -> None:
    body = (SKILLS_ROOT / "start-task" / "SKILL.md").read_text()
    assert "provider-neutral callback contract" in body


def test_runtime_docs_mention_codex_executor() -> None:
    body = Path("CLAUDE.md").read_text()
    assert "Codex" in body
```

- [ ] **Step 2: Run the skills and doc-adjacent tests to verify they fail**

Run: `uv run pytest tests/test_skills.py -q`
Expected: FAIL because the skill text and docs are still Claude-only.

- [ ] **Step 3: Update docs and skill wording**

```markdown
The `--from-file` form is the stable callback contract for all executors. It is
also required for Claude because its command permission matcher rejects
multi-line `opc` invocations.
```

```markdown
Each agent may run on Claude Code or Codex. Executor selection lives in
`agent.yaml`; missing `executor` defaults to `claude`.
```

- [ ] **Step 4: Run the skills tests and the full suite**

Run: `uv run pytest tests/ -q`
Expected: PASS with the same baseline count plus any new tests added for multi-executor support.

- [ ] **Step 5: Commit**

```bash
git add README.md CLAUDE.md protocol/05a-teams.md protocol/05b-agent-runtime.md protocol/05c-orchestrator.md protocol/skills/start-task/SKILL.md protocol/skills/manage-repo/SKILL.md protocol/skills/manage-agent/SKILL.md tests/test_skills.py
git commit -m "docs: document mixed Claude and Codex executors"
```

## Self-Review

### Spec coverage

- Per-agent executor selection: covered by Task 1 and Task 4.
- Default-to-Claude backward compatibility: covered by Task 1, Task 4, and Task 5.
- Provider-specific workspace bootstrap: covered by Task 2 and Task 4.
- Provider-specific subprocess launch: covered by Task 3 and Task 4.
- Shared SOP with provider-specific adapters: covered by Task 2 and Task 6.
- Mixed-fleet integration tests: covered by Task 5.
- Documentation updates: covered by Task 6.

### Placeholder scan

- No `TODO`, `TBD`, or “implement later” placeholders remain.
- Each task includes concrete files, commands, and expected outcomes.

### Type consistency

- `executor` is the single field name used across `agent.yaml`, enrollment payloads, and tests.
- Provider names are consistently `claude` and `codex`.
- Workspace bootstrap abstraction uses `prepare_workspace()` and `readiness_marker()` consistently.

