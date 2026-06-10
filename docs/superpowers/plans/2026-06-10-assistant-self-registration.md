# Assistant Self-Registration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the system assistant's executor *probing* with CLI *self-registration*: the founder opens their own agentic CLI in the assistant workspace, and the agent registers itself by calling `happyranch assistant register`.

**Architecture:** `assistant init` prepares the workspace and writes registration instructions to both `CLAUDE.md` and `AGENTS.md`; the agent declares `{executor, command, argv}` via a `--from-file` callback to `POST /assistant/register`; the daemon validates structurally (executable resolves, argv well-formed), runs the existing `bootstrap_assistant_workspace`, saves config, and the assistant becomes `configured`. The PTY-fork probe machinery is deleted; the runtime *attach* PTY session is untouched.

**Tech Stack:** Python 3, FastAPI, Pydantic v2, pytest. CLI is a thin HTTP client.

**Spec:** `docs/superpowers/specs/2026-06-10-assistant-self-registration-design.md`

---

## File Structure

Files created or modified, by responsibility:

- `runtime/system_assistant.py` — **modify.** Executor becomes a free `str` (drop `AssistantExecutor` enum); drop `latest_probe_results`; add `prepare_assistant_registration_workspace`, `_registration_prompt`, `clear_assistant_config`.
- `runtime/daemon/assistant_pty.py` — **modify (delete probe code).** Remove `InteractiveExecutorSpec`, `ProbeResult`, `ProbeRunner`, `build_executor_specs`, `build_probe_request/response`, `PROBE_REQUEST/READY`. Keep `AssistantPtySession`, `AssistantSessionManager`, `_set_pty_window_size`.
- `runtime/daemon/routes/assistant.py` — **modify.** Remove `/probes` and `/configure` (+ probe helpers); add `/init` and `/register`.
- `runtime/config.py` — **modify.** Drop `assistant_probe_timeout_seconds`.
- `cli/commands/assistant.py` — **modify.** New `init` behavior (calls `/assistant/init`); new `register` subcommand; drop probe/`_choose_executor` flow.
- `tests/test_system_assistant.py` — **modify.** Free-string executor; new prep/clear functions.
- `tests/test_assistant_pty.py` — **modify.** Drop probe tests.
- `tests/daemon/test_routes_assistant.py` — **modify.** Replace probe/configure tests with init/register tests.
- `tests/test_config.py` — **modify.** Drop probe-timeout assertion.
- `tests/contract/openapi.json` — **regenerate.**
- `web/src/test/openapi-coverage.test.ts` — **modify.** Update CLI-only allowlist.

Task order is dependency-driven: data model → workspace prep → delete probe lib → routes → CLI → config → contract/docs.

---

## Task 1: Free-string executor + drop probe fields in `system_assistant.py`

**Files:**
- Modify: `runtime/system_assistant.py`
- Test: `tests/test_system_assistant.py`

- [ ] **Step 1: Write failing tests for free-string executor**

Add to `tests/test_system_assistant.py`:

```python
def test_assistant_config_accepts_arbitrary_executor_string(tmp_path: Path) -> None:
    from runtime.system_assistant import AssistantConfig

    config = AssistantConfig(
        selected_executor="my-custom-cli",
        selected_command="my-custom-cli",
        selected_argv=["my-custom-cli"],
        workspace_path=str(tmp_path / "ws"),
    )
    assert config.selected_executor == "my-custom-cli"


def test_assistant_config_rejects_empty_executor(tmp_path: Path) -> None:
    import pytest
    from pydantic import ValidationError
    from runtime.system_assistant import AssistantConfig

    with pytest.raises(ValidationError):
        AssistantConfig(
            selected_executor="",
            selected_command="claude",
            selected_argv=["claude"],
            workspace_path=str(tmp_path / "ws"),
        )


def test_assistant_config_has_no_probe_results_field() -> None:
    from runtime.system_assistant import AssistantConfig

    assert "latest_probe_results" not in AssistantConfig.model_fields
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_system_assistant.py::test_assistant_config_accepts_arbitrary_executor_string tests/test_system_assistant.py::test_assistant_config_has_no_probe_results_field -v`
Expected: FAIL (custom string raises ValidationError against the enum; `latest_probe_results` still present).

- [ ] **Step 3: Remove the `AssistantExecutor` enum**

In `runtime/system_assistant.py`, delete:

```python
class AssistantExecutor(StrEnum):
    CLAUDE = "claude"
    CODEX = "codex"
    OPENCODE = "opencode"
    PI = "pi"
```

The `StrEnum` import is still used by `AssistantState`; leave it.

- [ ] **Step 4: Make executor a validated string and drop probe results on the models**

Replace the `AssistantConfig` model body with:

```python
class AssistantConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_executor: str
    selected_command: str
    selected_argv: list[str] = Field(default_factory=list)
    workspace_path: str

    @model_validator(mode="after")
    def _normalize(self) -> AssistantConfig:
        if not self.selected_executor.strip():
            raise ValueError("selected_executor must be a non-empty string")
        if not self.selected_argv:
            self.selected_argv = [self.selected_command]
        return self
```

Replace the `AssistantStatus` model body with:

```python
class AssistantStatus(BaseModel):
    state: AssistantState
    selected_executor: str | None = None
    workspace_path: str | None = None
    detail: str | None = None
```

- [ ] **Step 5: Drop `latest_probe_results` from every `classify_assistant_state` return**

In `classify_assistant_state`, remove every occurrence of the exact line (it appears in multiple `AssistantStatus(...)` returns):

```python
            latest_probe_results=config.latest_probe_results,
```

Delete that argument line in all returns. No other change to the surrounding logic.

- [ ] **Step 6: Replace executor validation and the CLAUDE/AGENTS branch**

Replace `_validate_executor`:

```python
def _validate_executor(executor: str) -> str:
    value = executor.strip()
    if not value:
        raise ValueError("assistant executor must be a non-empty string")
    return value
```

In `classify_assistant_state`, replace:

```python
    expected = (
        "CLAUDE.md"
        if config.selected_executor == AssistantExecutor.CLAUDE
        else "AGENTS.md"
    )
```

with:

```python
    expected = "CLAUDE.md" if config.selected_executor == "claude" else "AGENTS.md"
```

In `bootstrap_assistant_workspace`, the line `selected_executor = _validate_executor(executor)` now yields a `str`. Replace `"executor": selected_executor.value,` (in the `agent.yaml` dump) with `"executor": selected_executor,`, and replace:

```python
    if selected_executor == AssistantExecutor.CLAUDE:
```

with:

```python
    if selected_executor == "claude":
```

- [ ] **Step 7: Update existing tests that reference `AssistantExecutor` / `latest_probe_results`**

Run: `grep -rn "AssistantExecutor\|latest_probe_results" tests/test_system_assistant.py`
For each hit: replace `AssistantExecutor.CLAUDE` with the literal `"claude"` (and the other members with their string values), and delete assertions/keyword args referencing `latest_probe_results`.

- [ ] **Step 8: Run the system-assistant tests**

Run: `uv run python -m pytest tests/test_system_assistant.py -v`
Expected: PASS (including the three new tests).

- [ ] **Step 9: Commit**

```bash
git add runtime/system_assistant.py tests/test_system_assistant.py
git commit -m "refactor(assistant): make executor a free string and drop probe-result fields"
```

---

## Task 2: Registration workspace prep + clear-config in `system_assistant.py`

**Files:**
- Modify: `runtime/system_assistant.py`
- Test: `tests/test_system_assistant.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_system_assistant.py`:

```python
def test_prepare_registration_workspace_writes_both_prompt_files(tmp_path: Path) -> None:
    from runtime.system_assistant import (
        prepare_assistant_registration_workspace,
        system_assistant_paths,
    )

    prepare_assistant_registration_workspace(tmp_path)

    paths = system_assistant_paths(tmp_path)
    claude = (paths.workspace / "CLAUDE.md").read_text()
    agents = (paths.workspace / "AGENTS.md").read_text()
    assert "happyranch assistant register --from-file" in claude
    assert claude == agents


def test_clear_assistant_config_removes_config_file(tmp_path: Path) -> None:
    from runtime.system_assistant import (
        AssistantConfig,
        clear_assistant_config,
        load_assistant_config,
        save_assistant_config,
        system_assistant_paths,
    )

    paths = system_assistant_paths(tmp_path)
    save_assistant_config(
        tmp_path,
        AssistantConfig(
            selected_executor="claude",
            selected_command="claude",
            selected_argv=["claude"],
            workspace_path=str(paths.workspace),
        ),
    )
    assert paths.config_path.exists()

    clear_assistant_config(tmp_path)
    assert not paths.config_path.exists()
    assert load_assistant_config(tmp_path) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_system_assistant.py::test_prepare_registration_workspace_writes_both_prompt_files tests/test_system_assistant.py::test_clear_assistant_config_removes_config_file -v`
Expected: FAIL with ImportError (functions undefined).

- [ ] **Step 3: Add the registration prompt and prep/clear functions**

Add to `runtime/system_assistant.py` (near `_assistant_prompt`):

```python
def _registration_prompt() -> str:
    return """# Register as the HappyRanch System Assistant

You have been opened in the HappyRanch system assistant workspace, but no
assistant is configured yet. Register yourself so HappyRanch can re-launch you
for future sessions.

Steps:
1. Write a JSON file (for example `register.json`) in this workspace with:
   {
     "executor": "<your CLI name, e.g. claude>",
     "command": "<the command that launches you, e.g. claude>",
     "argv": ["<command>", "<any>", "<args>"]
   }
2. Run this exact single-line command:
   happyranch assistant register --from-file register.json

After registration succeeds, this file is replaced with your operating
instructions.
"""


def prepare_assistant_registration_workspace(runtime_root: Path) -> None:
    paths = system_assistant_paths(runtime_root)
    _reject_symlink(
        paths.root.parent,
        "assistant system directory must not be a symlink",
    )
    _reject_symlink(paths.root, "assistant root must not be a symlink")
    _reject_symlink(paths.workspace, "assistant workspace must not be a symlink")
    _reject_existing_invalid_bootstrap_file(
        paths.workspace / "AGENTS.md",
        "AGENTS.md",
    )
    _reject_existing_invalid_bootstrap_file(
        paths.workspace / "CLAUDE.md",
        "CLAUDE.md",
    )
    _ensure_managed_dir(
        paths.root.parent,
        "assistant system directory must not be a symlink",
        "assistant system directory is not a directory",
    )
    _ensure_managed_dir(
        paths.root,
        "assistant root must not be a symlink",
        "assistant root is not a directory",
    )
    _ensure_managed_dir(
        paths.workspace,
        "assistant workspace must not be a symlink",
        "assistant workspace is not a directory",
    )
    prompt = _registration_prompt()
    (paths.workspace / "CLAUDE.md").write_text(prompt)
    (paths.workspace / "AGENTS.md").write_text(prompt)


def clear_assistant_config(runtime_root: Path) -> None:
    paths = system_assistant_paths(runtime_root)
    if paths.config_path.is_symlink():
        raise ValueError("assistant config must not be a symlink")
    paths.config_path.unlink(missing_ok=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_system_assistant.py::test_prepare_registration_workspace_writes_both_prompt_files tests/test_system_assistant.py::test_clear_assistant_config_removes_config_file -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/system_assistant.py tests/test_system_assistant.py
git commit -m "feat(assistant): add registration workspace prep and config clear helpers"
```

---

## Task 3: Delete probe machinery from `assistant_pty.py`

**Files:**
- Modify: `runtime/daemon/assistant_pty.py`
- Test: `tests/test_assistant_pty.py`

- [ ] **Step 1: Delete the probe-only symbols**

In `runtime/daemon/assistant_pty.py`, delete these definitions entirely:
- The constants `PROBE_REQUEST` and `PROBE_READY` (the two lines near the top).
- `class InteractiveExecutorSpec` (dataclass).
- `class ProbeResult` (dataclass).
- `def build_probe_request(...)`.
- `def build_probe_response(...)`.
- `def build_executor_specs(...)`.
- `class ProbeRunner:` and its entire body (to end of file or next top-level symbol).

Keep everything else, especially: `_set_pty_window_size`, `_DEFAULT_PTY_ROWS`, `_DEFAULT_PTY_COLS`, `_close_fd`, `_terminate_process`, `_parse_selected_command`, `_build_session_launch_argv`, `class AssistantPtySession`, `class AssistantSessionManager`.

- [ ] **Step 2: Remove now-unused imports**

Run: `uv run python -c "import ast,sys; ast.parse(open('runtime/daemon/assistant_pty.py').read())"` (syntax check).
Then run `uv run ruff check runtime/daemon/assistant_pty.py` if ruff is available, else manually verify: `tempfile` and the `Settings` import (`from runtime.config import Settings`) were used only by probe code — remove them if no longer referenced. Confirm with:
`grep -n "tempfile\.\|Settings\b" runtime/daemon/assistant_pty.py`
Remove `import tempfile` and `from runtime.config import Settings` only if grep shows no remaining use.

- [ ] **Step 3: Delete probe tests in `tests/test_assistant_pty.py`**

Run: `grep -n "def test_\|ProbeRunner\|build_executor_specs\|build_probe_\|InteractiveExecutorSpec\|ProbeResult\|PROBE_REQUEST\|PROBE_READY" tests/test_assistant_pty.py`
Delete every test function that references `ProbeRunner`, `build_executor_specs`, `build_probe_request`, `build_probe_response`, `InteractiveExecutorSpec`, `ProbeResult`, `PROBE_REQUEST`, or `PROBE_READY`, plus any now-unused imports of those names. Keep all tests that exercise `AssistantPtySession` / `AssistantSessionManager` / `_set_pty_window_size`.

- [ ] **Step 4: Run the PTY tests**

Run: `uv run python -m pytest tests/test_assistant_pty.py -v`
Expected: PASS (session tests only; no import errors).

- [ ] **Step 5: Commit**

```bash
git add runtime/daemon/assistant_pty.py tests/test_assistant_pty.py
git commit -m "refactor(assistant): delete PTY probe machinery, keep attach session"
```

---

## Task 4: Replace `/probes` + `/configure` with `/init` + `/register` routes

**Files:**
- Modify: `runtime/daemon/routes/assistant.py`
- Test: `tests/daemon/test_routes_assistant.py`

- [ ] **Step 1: Write failing route tests**

In `tests/daemon/test_routes_assistant.py`, add (using the existing `client` fixture, which is a configured-runtime `TestClient`):

```python
def test_assistant_register_configures_with_valid_payload(client: TestClient) -> None:
    response = client.post(
        "/api/v1/assistant/register",
        json={"executor": "claude", "command": "sh", "argv": ["sh"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "configured"
    assert body["selected_executor"] == "claude"


def test_assistant_register_rejects_missing_executable(client: TestClient) -> None:
    response = client.post(
        "/api/v1/assistant/register",
        json={
            "executor": "ghost",
            "command": "definitely-not-a-real-binary-xyz",
            "argv": ["definitely-not-a-real-binary-xyz"],
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "assistant_executable_not_found"


def test_assistant_register_rejects_empty_executor(client: TestClient) -> None:
    response = client.post(
        "/api/v1/assistant/register",
        json={"executor": "  ", "command": "sh", "argv": ["sh"]},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "assistant_registration_invalid"


def test_assistant_register_rejects_extra_fields(client: TestClient) -> None:
    response = client.post(
        "/api/v1/assistant/register",
        json={"executor": "claude", "command": "sh", "argv": ["sh"], "x": 1},
    )
    assert response.status_code == 422


def test_assistant_init_prepares_registration_workspace(client: TestClient) -> None:
    response = client.post("/api/v1/assistant/init", json={})
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "uninitialized"


def test_assistant_init_reconfigure_clears_existing_config(client: TestClient) -> None:
    configured = client.post(
        "/api/v1/assistant/register",
        json={"executor": "claude", "command": "sh", "argv": ["sh"]},
    )
    assert configured.json()["state"] == "configured"

    response = client.post("/api/v1/assistant/init", json={"reconfigure": True})
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "uninitialized"
```

Use `command: "sh"` because `sh` resolves via `shutil.which` on the test host and `bootstrap_assistant_workspace` accepts any string executor.

Then DELETE the existing probe/configure tests in this file: every test whose name starts with `test_assistant_probes` or `test_assistant_configure`, plus the now-unused probe helpers `_passed_probe_result`, `_patch_probe_runner`, `_daemon_probe_result`, `_expected_resolved_argv`, and any `FakeProbeRunner` classes / `build_executor_specs` monkeypatches.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/daemon/test_routes_assistant.py -k "register or init" -v`
Expected: FAIL (routes return 404 / not found).

- [ ] **Step 3: Update imports and request models in the route module**

In `runtime/daemon/routes/assistant.py`, replace the `assistant_pty` import block:

```python
from runtime.daemon.assistant_pty import (
    AssistantPtySession,
    InteractiveExecutorSpec,
    ProbeResult,
    ProbeRunner,
    build_executor_specs,
)
```

with:

```python
from runtime.daemon.assistant_pty import AssistantPtySession
```

Add `prepare_assistant_registration_workspace` and `clear_assistant_config` to the `runtime.system_assistant` import block.

Replace `class ConfigureAssistantRequest` and `class ProbeResultRow` with:

```python
class InitAssistantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reconfigure: bool = False


class RegisterAssistantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    executor: str
    command: str
    argv: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Delete probe helper functions**

Delete these functions from `runtime/daemon/routes/assistant.py`: `_probe_result_to_dict`, `_spec_with_argv`, `_resolved_argv_for_spec`, `_spec_for_executor`, `_server_selected_command`, `_normalize_probe_results`, `_probe_selected_executor`. Keep `_runtime_root`, `_require_current_runtime_root`, `_assistant_error`, and all websocket/session helpers.

Remove the now-unused `import asyncio`? No — `asyncio` is still used by the websocket pump. Remove `import os` only if unused after deletions (`grep -n "os\." runtime/daemon/routes/assistant.py`). Keep `import shutil` (used by the new validation).

- [ ] **Step 5: Replace the `/probes` and `/configure` routes with `/init` and `/register`**

Delete the `@router.post("/assistant/probes" ...)` and `@router.post("/assistant/configure" ...)` route functions. In their place add:

```python
@router.post("/assistant/init", dependencies=[require_token()])
async def init_assistant(
    body: InitAssistantRequest,
    request: Request,
) -> dict[str, Any]:
    root = _runtime_root(request)
    state: DaemonState = request.app.state.daemon
    try:
        async with state.assistant_lifecycle_lock:
            root = _require_current_runtime_root(state, root)
            current = classify_assistant_state(root)
            if current.state == AssistantState.CONFIGURED and not body.reconfigure:
                return current.model_dump()
            if body.reconfigure:
                await state.assistant_sessions.close_all()
                clear_assistant_config(root)
            prepare_assistant_registration_workspace(root)
    except ValueError as exc:
        raise _assistant_error("assistant_workspace_invalid", exc) from exc
    return classify_assistant_state(root).model_dump()


@router.post("/assistant/register", dependencies=[require_token()])
async def register_assistant(
    body: RegisterAssistantRequest,
    request: Request,
) -> dict[str, Any]:
    root = _runtime_root(request)
    state: DaemonState = request.app.state.daemon

    executor = body.executor.strip()
    command = body.command.strip()
    argv = [a for a in body.argv if a and a.strip()] or ([command] if command else [])
    if not executor or not command or not argv:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "assistant_registration_invalid",
                "message": "executor, command, and argv must be non-empty",
            },
        )
    if shutil.which(argv[0]) is None:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "assistant_executable_not_found",
                "executable": argv[0],
            },
        )

    paths = system_assistant_paths(root)
    try:
        config = AssistantConfig(
            selected_executor=executor,
            selected_command=command,
            selected_argv=argv,
            workspace_path=str(paths.workspace),
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "assistant_registration_invalid", "message": str(exc)},
        ) from exc

    try:
        async with state.assistant_lifecycle_lock:
            root = _require_current_runtime_root(state, root)
            paths = system_assistant_paths(root)
            config = AssistantConfig(
                selected_executor=executor,
                selected_command=command,
                selected_argv=argv,
                workspace_path=str(paths.workspace),
            )
            await state.assistant_sessions.close_all()
            bootstrap_assistant_workspace(root, executor=executor)
            save_assistant_config(root, config)
    except ValueError as exc:
        raise _assistant_error("assistant_workspace_invalid", exc) from exc
    return classify_assistant_state(root).model_dump()
```

- [ ] **Step 6: Run the route tests**

Run: `uv run python -m pytest tests/daemon/test_routes_assistant.py -v`
Expected: PASS (new init/register tests pass; no leftover probe-test import errors).

- [ ] **Step 7: Commit**

```bash
git add runtime/daemon/routes/assistant.py tests/daemon/test_routes_assistant.py
git commit -m "feat(assistant): replace probe/configure routes with init/register"
```

---

## Task 5: CLI — new `init` behavior and `register` subcommand

**Files:**
- Modify: `cli/commands/assistant.py`
- Test: `tests/daemon/test_routes_assistant.py` (route-level coverage already added in Task 4; CLI is a thin client)

- [ ] **Step 1: Rewrite `cmd_assistant_init`**

In `cli/commands/assistant.py`, replace the entire `cmd_assistant_init` function with:

```python
def cmd_assistant_init(args: argparse.Namespace) -> None:
    client = _client()
    if args.repair and not args.reconfigure:
        r = client.post("/api/v1/assistant/repair")
        if r.status_code != 200:
            print(f"Error ({r.status_code}): {r.text}")
            sys.exit(1)
        _print_status(r.json())
        return
    r = client.post(
        "/api/v1/assistant/init",
        json={"reconfigure": bool(args.reconfigure)},
    )
    if r.status_code != 200:
        print(f"Error ({r.status_code}): {r.text}")
        sys.exit(1)
    body = r.json()
    _print_status(body)
    if body["state"] != "configured":
        workspace = body.get("workspace_path") or "<runtime>/system/assistant/workspace"
        print()
        print("Next steps to register your assistant CLI:")
        print(f"1. Open your agentic CLI (claude, codex, opencode, pi, ...) in:")
        print(f"     {workspace}")
        print("2. Ask it to register itself; it will run:")
        print("     happyranch assistant register --from-file <payload.json>")
```

Note: `_print_status` currently prints `latest_probe_results`? It does not — re-check `_print_status` and remove any `latest_probe_results` reference if present (it only prints state/executor/workspace/detail). Leave as-is if clean.

- [ ] **Step 2: Add `cmd_assistant_register`**

Add to `cli/commands/assistant.py`:

```python
def cmd_assistant_register(args: argparse.Namespace) -> None:
    client = _client()
    import json as _json

    if args.from_file:
        try:
            body = _json.loads(Path(args.from_file).read_text())
        except (OSError, _json.JSONDecodeError, ValueError) as exc:
            print(f"Error reading register file {args.from_file}: {exc}")
            sys.exit(1)
    else:
        body = {
            "executor": args.executor,
            "command": args.command,
            "argv": _json.loads(args.argv) if args.argv else [],
        }

    r = client.post("/api/v1/assistant/register", json=body)
    if r.status_code != 200:
        print(f"Error ({r.status_code}): {r.text}")
        sys.exit(1)
    _print_status(r.json())
```

Add `from pathlib import Path` to the imports at the top of the file if not already present (it is used by `_json.loads(Path(...))`). Check: `grep -n "^from pathlib import Path\|^import" cli/commands/assistant.py`; add `from pathlib import Path` if missing.

- [ ] **Step 3: Remove the dead probe helpers in the CLI**

Delete `_probe_passed`, `_probe_failure_reason`, and `_choose_executor` from `cli/commands/assistant.py` (they served the removed probe flow). Confirm no remaining references: `grep -n "_choose_executor\|_probe_passed\|_probe_failure_reason" cli/commands/assistant.py`.

- [ ] **Step 4: Register the `register` subcommand in argparse**

In `cli/commands/assistant.py`, inside `register(...)`, after the `p_init` block add:

```python
    p_register = assistant_sub.add_parser(
        "register",
        help="register the current agentic CLI as the system assistant",
    )
    p_register.add_argument(
        "--from-file",
        dest="from_file",
        default=None,
        help="Path to a JSON file with {executor, command, argv}",
    )
    p_register.add_argument("--executor", default=None)
    p_register.add_argument("--command", default=None)
    p_register.add_argument(
        "--argv",
        default=None,
        help="JSON array string for argv (e.g. '[\"claude\"]')",
    )
    p_register.set_defaults(func=cmd_assistant_register)
```

Add the import for `cmd_assistant_register` wherever `cmd_assistant_init` is imported (`grep -n "cmd_assistant_init" cli/main.py`); add `cmd_assistant_register` to that import list if commands are imported by name in `cli/main.py`. (If `cli/commands/assistant.py` self-registers via its own `register(sub)`, no `main.py` change is needed — verify with `grep -n "assistant" cli/main.py`.)

- [ ] **Step 5: Smoke-test the CLI wiring**

Run: `uv run python -m happyranch assistant register --help` (or `uv run python -m cli.main assistant register --help` if that is the module entrypoint — check `grep -n "def main" cli/main.py`).
Expected: help text shows `--from-file`, `--executor`, `--command`, `--argv`. No import errors.

- [ ] **Step 6: Commit**

```bash
git add cli/commands/assistant.py cli/main.py
git commit -m "feat(assistant): CLI register subcommand and registration-based init"
```

---

## Task 6: Drop `assistant_probe_timeout_seconds` from settings

**Files:**
- Modify: `runtime/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Confirm the setting is now unused**

Run: `grep -rn "assistant_probe_timeout_seconds" runtime/ cli/ tests/`
Expected: only `runtime/config.py:60` and `tests/test_config.py` remain (all daemon/route usages removed in Task 4). If any non-test runtime usage remains, fix that call site first.

- [ ] **Step 2: Remove the setting**

In `runtime/config.py`, delete the line:

```python
    assistant_probe_timeout_seconds: float = Field(default=15.0, gt=0)
```

- [ ] **Step 3: Update the config test**

Run: `grep -n "assistant_probe_timeout_seconds" tests/test_config.py`
Delete the assertion(s) referencing it.

- [ ] **Step 4: Run config tests**

Run: `uv run python -m pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/config.py tests/test_config.py
git commit -m "chore(config): drop unused assistant_probe_timeout_seconds"
```

---

## Task 7: Regenerate OpenAPI snapshot + update web coverage allowlist

**Files:**
- Regenerate: `tests/contract/openapi.json`
- Modify: `web/src/test/openapi-coverage.test.ts`

- [ ] **Step 1: Update the CLI-only allowlist**

In `web/src/test/openapi-coverage.test.ts`, replace these two lines:

```ts
  ['POST /api/v1/assistant/probes', 'system assistant CLI setup probes only'],
  ['POST /api/v1/assistant/configure', 'system assistant CLI setup only'],
```

with:

```ts
  ['POST /api/v1/assistant/init', 'system assistant CLI registration setup only'],
  ['POST /api/v1/assistant/register', 'system assistant CLI registration callback only'],
```

(Leave the `status` and `repair` allowlist lines unchanged.)

- [ ] **Step 2: Regenerate the OpenAPI snapshot**

Run: `HAPPYRANCH_REGEN_OPENAPI=1 uv run python -m pytest tests/contract/test_openapi_snapshot.py -v`
Expected: PASS; `tests/contract/openapi.json` updated (no `/assistant/probes` or `/assistant/configure`; now has `/assistant/init` and `/assistant/register`).

- [ ] **Step 3: Verify the snapshot is stable on a second run**

Run: `uv run python -m pytest tests/contract/test_openapi_snapshot.py -v`
Expected: PASS without regen (snapshot matches generated spec).

- [ ] **Step 4: Run the web coverage test**

Run: `cd web && npm run test -- openapi-coverage` (or the project's documented command for a single vitest file; check `web/package.json` scripts).
Expected: PASS (every route mapped to a TS function or allowlisted).

- [ ] **Step 5: Commit**

```bash
git add tests/contract/openapi.json web/src/test/openapi-coverage.test.ts
git commit -m "test(contract): update assistant routes in openapi snapshot and coverage"
```

---

## Task 8: Full suite, docs, and final verification

**Files:**
- Modify (docs): `docs/agent-guides/features-and-invariants.md` (assistant section, if it documents probing)

- [ ] **Step 1: Update the agent guide if it references probing**

Run: `grep -rn "probe\|assistant probes\|assistant configure\|assistant init" docs/agent-guides/`
For any assistant onboarding text that describes probing/`configure`, replace with the registration flow: `init` prepares the workspace + writes registration instructions; the agent calls `happyranch assistant register --from-file <payload>`; the daemon validates structurally and auto-configures. Keep edits surgical — only the assistant onboarding paragraphs.

- [ ] **Step 2: Run the full unit suite**

Run: `uv run python -m pytest tests/ -v`
Expected: PASS. Investigate and fix any residual references to removed symbols (`grep -rn "ProbeRunner\|build_executor_specs\|/assistant/probes\|/assistant/configure\|AssistantExecutor\|latest_probe_results\|assistant_probe_timeout_seconds" runtime/ cli/ tests/` should return nothing).

- [ ] **Step 3: Run integration tests (daemon lifecycle / callbacks touched)**

Run: `uv run python -m pytest tests/ -v -m integration`
Expected: PASS. (Self-registration touches assistant routes and callback shapes; integration covers daemon lifespan.)

- [ ] **Step 4: Manual end-to-end smoke (optional but recommended)**

```bash
scripts/daemon.sh start
happyranch assistant init
# In the printed workspace, run an agentic CLI and have it write payload.json:
#   {"executor":"claude","command":"claude","argv":["claude"]}
# then:  happyranch assistant register --from-file payload.json
happyranch assistant status   # expect: state: configured, executor: claude
```

- [ ] **Step 5: Commit any doc changes**

```bash
git add docs/agent-guides/features-and-invariants.md
git commit -m "docs(assistant): document self-registration onboarding"
```

---

## Notes for the implementer

- **TDD:** Each task writes the failing test first. Do not skip the "verify it fails" step — it catches wiring mistakes.
- **`sh` as a stand-in executable:** route tests use `command: "sh"` so `shutil.which` resolves on CI without depending on claude/codex being installed. `bootstrap_assistant_workspace` accepts any executor string.
- **Lifecycle lock:** both `/init` (reconfigure) and `/register` mutate workspace/config and therefore close sessions under `state.assistant_lifecycle_lock` and re-check the runtime root with `_require_current_runtime_root`, matching the old `/configure` pattern.
- **Do not touch** `AssistantPtySession` / `AssistantSessionManager` / `_set_pty_window_size` — the attach path is out of scope.
- **GitNexus:** run `gitnexus_impact` on `classify_assistant_state`, `bootstrap_assistant_workspace`, and `AssistantConfig` before editing (per repo CLAUDE.md), and `gitnexus_detect_changes()` before each commit.
