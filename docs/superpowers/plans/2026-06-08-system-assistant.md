# System Assistant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a runtime-global system assistant that is initialized through a fixed PTY request/reply probe, stores runtime-level assistant config, and can be attached through a daemon-owned interactive PTY session.

**Architecture:** Add a new runtime-level assistant module separate from org state and normal task executors. The daemon exposes container-level assistant status/probe/configure routes plus a WebSocket attach route; the CLI owns the interactive executor selection UX. PTY probing and long-lived attach use a new interactive executor layer, not `Executor.run()`.

**Tech Stack:** Python 3.11, FastAPI WebSocket, stdlib `pty`/`os`/`select`, argparse, httpx, `websockets` client library, pytest/FastAPI TestClient.

---

## File Structure

- Create `runtime/system_assistant.py`
  - Runtime-level paths, config models, JSON read/write, assistant state classification, bootstrap file writers.
- Create `runtime/daemon/assistant_pty.py`
  - Interactive executor specs, fixed probe runner, daemon-owned PTY session class, single-attach session manager.
- Create `runtime/daemon/routes/assistant.py`
  - `GET /api/v1/assistant/status`, `POST /api/v1/assistant/probes`, `POST /api/v1/assistant/configure`, `POST /api/v1/assistant/repair`, `WebSocket /api/v1/assistant/session`.
- Modify `runtime/daemon/app.py`
  - Include the assistant router.
- Modify `runtime/daemon/state.py`
  - Add an assistant session manager field and close it on daemon shutdown.
- Create `cli/commands/assistant.py`
  - `happyranch assistant init`, `happyranch assistant status`, `happyranch assistant attach`; bare `happyranch assistant` aliases to `attach`.
- Modify `cli/main.py`
  - Import/register assistant commands and re-export handlers for tests.
- Modify `pyproject.toml`
  - Add direct dependency on `websockets>=12` for the CLI WebSocket client.
- Create tests:
  - `tests/test_system_assistant.py`
  - `tests/daemon/test_routes_assistant.py`
  - `tests/test_assistant_pty.py`
  - `tests/test_cli_assistant.py`

## Task 1: Runtime Assistant Config And Bootstrap

**Files:**
- Create: `runtime/system_assistant.py`
- Test: `tests/test_system_assistant.py`

- [ ] **Step 1: Write failing tests for paths, state, config persistence, and bootstrap**

Create `tests/test_system_assistant.py`:

```python
from __future__ import annotations

from pathlib import Path

from runtime.system_assistant import (
    AssistantConfig,
    AssistantState,
    bootstrap_assistant_workspace,
    classify_assistant_state,
    load_assistant_config,
    save_assistant_config,
    system_assistant_paths,
)


def test_system_assistant_paths_are_runtime_global(tmp_path: Path) -> None:
    paths = system_assistant_paths(tmp_path)

    assert paths.root == tmp_path / "system" / "assistant"
    assert paths.config_path == tmp_path / "system" / "assistant" / "config.json"
    assert paths.workspace == tmp_path / "system" / "assistant" / "workspace"
    assert "orgs" not in paths.root.parts


def test_classify_uninitialized_when_config_missing(tmp_path: Path) -> None:
    assert classify_assistant_state(tmp_path).state == AssistantState.UNINITIALIZED


def test_save_and_load_config_round_trips(tmp_path: Path) -> None:
    cfg = AssistantConfig(
        selected_executor="codex",
        selected_command="codex",
        workspace_path=str(tmp_path / "system" / "assistant" / "workspace"),
        latest_probe_results=[
            {
                "executor": "codex",
                "status": "passed",
                "command": "codex",
                "checked_at": "2026-06-08T00:00:00Z",
                "latency_ms": 12,
            }
        ],
    )

    save_assistant_config(tmp_path, cfg)

    assert load_assistant_config(tmp_path) == cfg
    assert classify_assistant_state(tmp_path).state == AssistantState.STALE_OR_BROKEN


def test_bootstrap_codex_workspace_writes_agents_surface(tmp_path: Path) -> None:
    bootstrap_assistant_workspace(tmp_path, executor="codex")
    workspace = tmp_path / "system" / "assistant" / "workspace"

    assert (workspace / "agent.yaml").read_text().startswith("name: system_assistant\n")
    agents_md = (workspace / "AGENTS.md").read_text()
    assert "System Assistant" in agents_md
    assert "explicit user confirmation" in agents_md
    assert (workspace / "learnings" / "_index.md").exists()
    assert (workspace / "logs").is_dir()


def test_bootstrap_claude_workspace_writes_claude_surface(tmp_path: Path) -> None:
    bootstrap_assistant_workspace(tmp_path, executor="claude")

    workspace = tmp_path / "system" / "assistant" / "workspace"
    assert (workspace / "CLAUDE.md").exists()
    assert not (workspace / "AGENTS.md").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_system_assistant.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'runtime.system_assistant'`.

- [ ] **Step 3: Implement runtime config/bootstrap module**

Create `runtime/system_assistant.py`:

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class AssistantState(StrEnum):
    UNINITIALIZED = "uninitialized"
    CONFIGURED = "configured"
    STALE_OR_BROKEN = "stale_or_broken"


@dataclass(frozen=True)
class SystemAssistantPaths:
    root: Path
    config_path: Path
    workspace: Path
    learnings_dir: Path
    logs_dir: Path


class AssistantConfig(BaseModel):
    selected_executor: str
    selected_command: str
    workspace_path: str
    latest_probe_results: list[dict[str, Any]] = Field(default_factory=list)


class AssistantStatus(BaseModel):
    state: AssistantState
    selected_executor: str | None = None
    workspace_path: str | None = None
    detail: str | None = None
    latest_probe_results: list[dict[str, Any]] = Field(default_factory=list)


def system_assistant_paths(runtime_root: Path) -> SystemAssistantPaths:
    root = runtime_root / "system" / "assistant"
    workspace = root / "workspace"
    return SystemAssistantPaths(
        root=root,
        config_path=root / "config.json",
        workspace=workspace,
        learnings_dir=workspace / "learnings",
        logs_dir=workspace / "logs",
    )


def load_assistant_config(runtime_root: Path) -> AssistantConfig | None:
    path = system_assistant_paths(runtime_root).config_path
    if not path.exists():
        return None
    return AssistantConfig.model_validate_json(path.read_text())


def save_assistant_config(runtime_root: Path, config: AssistantConfig) -> None:
    paths = system_assistant_paths(runtime_root)
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.config_path.write_text(config.model_dump_json(indent=2) + "\n")


def classify_assistant_state(runtime_root: Path) -> AssistantStatus:
    paths = system_assistant_paths(runtime_root)
    config = load_assistant_config(runtime_root)
    if config is None:
        return AssistantStatus(state=AssistantState.UNINITIALIZED)
    if not paths.workspace.exists():
        return AssistantStatus(
            state=AssistantState.STALE_OR_BROKEN,
            selected_executor=config.selected_executor,
            workspace_path=config.workspace_path,
            detail="assistant workspace is missing",
            latest_probe_results=config.latest_probe_results,
        )
    expected = "CLAUDE.md" if config.selected_executor == "claude" else "AGENTS.md"
    if not (paths.workspace / expected).exists():
        return AssistantStatus(
            state=AssistantState.STALE_OR_BROKEN,
            selected_executor=config.selected_executor,
            workspace_path=config.workspace_path,
            detail=f"assistant bootstrap file {expected} is missing",
            latest_probe_results=config.latest_probe_results,
        )
    return AssistantStatus(
        state=AssistantState.CONFIGURED,
        selected_executor=config.selected_executor,
        workspace_path=config.workspace_path,
        latest_probe_results=config.latest_probe_results,
    )


def _assistant_prompt() -> str:
    return """# System Assistant

You are the HappyRanch system assistant. Help the founder operate HappyRanch itself:
setup, protocol explanation, runtime health, executor diagnosis, org discovery, and
guided next actions.

Authority boundary:
- Explain, inspect, and diagnose freely.
- Recommend next actions clearly.
- Run mutating HappyRanch commands only after explicit user confirmation.
- Do not silently edit runtime config, org definitions, agent files, or teams.
- Do not act as an org agent, team member, manager, or task worker.
"""


def bootstrap_assistant_workspace(runtime_root: Path, *, executor: str) -> None:
    paths = system_assistant_paths(runtime_root)
    paths.workspace.mkdir(parents=True, exist_ok=True)
    paths.learnings_dir.mkdir(parents=True, exist_ok=True)
    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    (paths.workspace / "agent.yaml").write_text(
        f"name: system_assistant\nexecutor: {executor}\nrepos: {{}}\n"
    )
    if not (paths.learnings_dir / "_index.md").exists():
        (paths.learnings_dir / "_index.md").write_text("# Learnings: system_assistant\n\n")
    prompt = _assistant_prompt()
    claude_path = paths.workspace / "CLAUDE.md"
    agents_path = paths.workspace / "AGENTS.md"
    if executor == "claude":
        agents_path.unlink(missing_ok=True)
        claude_path.write_text(prompt)
    else:
        claude_path.unlink(missing_ok=True)
        agents_path.write_text(prompt)
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
uv run pytest tests/test_system_assistant.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/system_assistant.py tests/test_system_assistant.py
git commit -m "feat(assistant): add runtime assistant config"
```

## Task 2: Interactive PTY Probe Layer

**Files:**
- Create: `runtime/daemon/assistant_pty.py`
- Test: `tests/test_assistant_pty.py`

- [ ] **Step 1: Write failing PTY probe tests with fake CLIs**

Create `tests/test_assistant_pty.py`:

```python
from __future__ import annotations

import os
from pathlib import Path

from runtime.config import Settings
from runtime.daemon.assistant_pty import (
    PROBE_READY,
    PROBE_REQUEST,
    ProbeRunner,
    build_executor_specs,
)


def _write_fake_cli(path: Path, body: str) -> str:
    path.write_text("#!/usr/bin/env python3\n" + body)
    path.chmod(path.stat().st_mode | 0o111)
    return str(path)


def test_probe_passes_when_marker_returned(tmp_path: Path) -> None:
    cli = _write_fake_cli(
        tmp_path / "fake_cli.py",
        """
import sys
for line in sys.stdin:
    if "HAPPYRANCH_PTY_PROBE_V1" in line:
        print("HAPPYRANCH_PTY_READY_V1", flush=True)
        break
""",
    )
    runner = ProbeRunner(timeout_seconds=2.0)

    result = runner.probe_executor("codex", command=cli, workspace_parent=tmp_path)

    assert result["executor"] == "codex"
    assert result["status"] == "passed"
    assert result["reason"] is None
    assert result["latency_ms"] >= 0


def test_probe_fails_on_wrong_marker(tmp_path: Path) -> None:
    cli = _write_fake_cli(
        tmp_path / "wrong_cli.py",
        """
print("NOT_READY", flush=True)
""",
    )

    result = ProbeRunner(timeout_seconds=0.5).probe_executor(
        "codex", command=cli, workspace_parent=tmp_path
    )

    assert result["status"] == "failed"
    assert result["reason"] in {"process_exited_before_probe_reply", "timeout_waiting_for_probe_reply"}


def test_probe_writes_minimal_workspace_surface(tmp_path: Path) -> None:
    cli = _write_fake_cli(
        tmp_path / "fake_cli.py",
        """
import os
import sys
assert os.path.exists("AGENTS.md")
for line in sys.stdin:
    if "HAPPYRANCH_PTY_PROBE_V1" in line:
        print("HAPPYRANCH_PTY_READY_V1", flush=True)
        break
""",
    )

    result = ProbeRunner(timeout_seconds=2.0).probe_executor(
        "codex", command=cli, workspace_parent=tmp_path
    )

    assert result["status"] == "passed"


def test_build_executor_specs_uses_settings_paths() -> None:
    settings = Settings(
        claude_cli_path="/bin/claude",
        codex_cli_path="/bin/codex",
        opencode_cli_path="/bin/opencode",
        pi_cli_path="/bin/pi",
    )

    specs = build_executor_specs(settings)

    assert specs["claude"].command == "/bin/claude"
    assert specs["claude"].bootstrap_file == "CLAUDE.md"
    assert specs["codex"].bootstrap_file == "AGENTS.md"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_assistant_pty.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'runtime.daemon.assistant_pty'`.

- [ ] **Step 3: Implement probe runner**

Create `runtime/daemon/assistant_pty.py` with this initial content:

```python
from __future__ import annotations

import os
import pty
import select
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.config import Settings

PROBE_REQUEST = "HAPPYRANCH_PTY_PROBE_V1"
PROBE_READY = "HAPPYRANCH_PTY_READY_V1"


@dataclass(frozen=True)
class InteractiveExecutorSpec:
    name: str
    command: str
    argv: tuple[str, ...]
    bootstrap_file: str
    hint: str


def build_executor_specs(settings: Settings) -> dict[str, InteractiveExecutorSpec]:
    return {
        "claude": InteractiveExecutorSpec(
            name="claude",
            command=settings.claude_cli_path,
            argv=(settings.claude_cli_path,),
            bootstrap_file="CLAUDE.md",
            hint="Run `claude` once locally and complete login, then retry `happyranch assistant init`.",
        ),
        "codex": InteractiveExecutorSpec(
            name="codex",
            command=settings.codex_cli_path,
            argv=(settings.codex_cli_path,),
            bootstrap_file="AGENTS.md",
            hint="Run `codex` once locally and complete login, then retry `happyranch assistant init`.",
        ),
        "opencode": InteractiveExecutorSpec(
            name="opencode",
            command=settings.opencode_cli_path,
            argv=(settings.opencode_cli_path,),
            bootstrap_file="AGENTS.md",
            hint="Run `opencode` once locally and complete login, then retry `happyranch assistant init`.",
        ),
        "pi": InteractiveExecutorSpec(
            name="pi",
            command=settings.pi_cli_path,
            argv=(settings.pi_cli_path,),
            bootstrap_file="AGENTS.md",
            hint="Run `pi` once locally and complete login, then retry `happyranch assistant init`.",
        ),
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _probe_prompt() -> str:
    return (
        "# HappyRanch PTY Probe\n\n"
        f"If the user sends `{PROBE_REQUEST}`, reply with exactly:\n\n"
        f"{PROBE_READY}\n"
    )


class ProbeRunner:
    def __init__(self, timeout_seconds: float = 20.0) -> None:
        self.timeout_seconds = timeout_seconds

    def probe_executor(
        self,
        executor: str,
        *,
        command: str,
        workspace_parent: Path,
    ) -> dict[str, Any]:
        bootstrap_file = "CLAUDE.md" if executor == "claude" else "AGENTS.md"
        workspace = workspace_parent / f"happyranch-pty-probe-{uuid.uuid4().hex}"
        workspace.mkdir(parents=True)
        (workspace / bootstrap_file).write_text(_probe_prompt())
        argv = [command]
        started = time.monotonic()
        output = ""
        master_fd: int | None = None
        proc: subprocess.Popen[bytes] | None = None
        try:
            master_fd, slave_fd = pty.openpty()
            env = {
                "HOME": os.environ.get("HOME", ""),
                "PATH": os.environ.get("PATH", ""),
                "TERM": os.environ.get("TERM", "xterm-256color"),
            }
            proc = subprocess.Popen(
                argv,
                cwd=str(workspace),
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                env=env,
                start_new_session=True,
            )
            os.close(slave_fd)
            os.write(master_fd, (PROBE_REQUEST + "\n").encode())
            deadline = started + self.timeout_seconds
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    reason = "process_exited_before_probe_reply"
                    return self._failed(executor, command, started, reason, output)
                readable, _, _ = select.select([master_fd], [], [], 0.05)
                if not readable:
                    continue
                chunk = os.read(master_fd, 4096).decode(errors="replace")
                output += chunk
                if PROBE_READY in output:
                    return {
                        "executor": executor,
                        "status": "passed",
                        "reason": None,
                        "command": command,
                        "checked_at": _now_iso(),
                        "latency_ms": int((time.monotonic() - started) * 1000),
                    }
            return self._failed(
                executor,
                command,
                started,
                "timeout_waiting_for_probe_reply",
                output,
            )
        except FileNotFoundError:
            return self._failed(executor, command, started, "command_not_found", output)
        finally:
            if proc is not None and proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            if master_fd is not None:
                try:
                    os.close(master_fd)
                except OSError:
                    pass

    def _failed(
        self,
        executor: str,
        command: str,
        started: float,
        reason: str,
        output: str,
    ) -> dict[str, Any]:
        return {
            "executor": executor,
            "status": "failed",
            "reason": reason,
            "command": command,
            "checked_at": _now_iso(),
            "latency_ms": int((time.monotonic() - started) * 1000),
            "stderr_tail": output[-2000:],
            "hint": f"Run `{executor}` once locally and complete login, then retry `happyranch assistant init`.",
        }
```

- [ ] **Step 4: Run probe tests**

Run:

```bash
uv run pytest tests/test_assistant_pty.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/daemon/assistant_pty.py tests/test_assistant_pty.py
git commit -m "feat(assistant): add PTY probe runner"
```

## Task 3: Daemon Assistant HTTP Routes

**Files:**
- Create: `runtime/daemon/routes/assistant.py`
- Modify: `runtime/daemon/app.py`
- Test: `tests/daemon/test_routes_assistant.py`

- [ ] **Step 1: Write failing route tests**

Create `tests/daemon/test_routes_assistant.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runtime.config import Settings
from runtime.daemon.app import create_app
from runtime.daemon.state import DaemonState
from runtime.runtime import RuntimeDir


@pytest.fixture
def auth(monkeypatch, tmp_path):
    home = tmp_path / "happyranch-home"
    home.mkdir()
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(home))
    from runtime.daemon import paths

    return {"Authorization": f"Bearer {paths.ensure_token()}"}


def test_assistant_status_no_active_runtime(auth) -> None:
    client = TestClient(create_app(DaemonState.idle(Settings())))

    r = client.get("/api/v1/assistant/status", headers=auth)

    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "no_active_runtime"


def test_assistant_status_uninitialized(tmp_path: Path, auth) -> None:
    rt = RuntimeDir.init(tmp_path / "rt")
    client = TestClient(create_app(DaemonState.from_runtime(rt, Settings())))

    r = client.get("/api/v1/assistant/status", headers=auth)

    assert r.status_code == 200
    assert r.json()["state"] == "uninitialized"


def test_assistant_configure_requires_passing_probe(tmp_path: Path, auth) -> None:
    rt = RuntimeDir.init(tmp_path / "rt")
    client = TestClient(create_app(DaemonState.from_runtime(rt, Settings())))

    r = client.post(
        "/api/v1/assistant/configure",
        headers=auth,
        json={
            "selected_executor": "codex",
            "probe_results": [
                {"executor": "codex", "status": "failed", "command": "codex"}
            ],
        },
    )

    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "selected_executor_not_probe_passed"


def test_assistant_configure_writes_workspace_and_status(tmp_path: Path, auth) -> None:
    rt = RuntimeDir.init(tmp_path / "rt")
    client = TestClient(create_app(DaemonState.from_runtime(rt, Settings())))

    r = client.post(
        "/api/v1/assistant/configure",
        headers=auth,
        json={
            "selected_executor": "codex",
            "probe_results": [
                {"executor": "codex", "status": "passed", "command": "codex"}
            ],
        },
    )

    assert r.status_code == 200
    assert r.json()["state"] == "configured"
    assert (rt.root / "system" / "assistant" / "config.json").exists()
    assert (rt.root / "system" / "assistant" / "workspace" / "AGENTS.md").exists()


def test_assistant_repair_refreshes_workspace(tmp_path: Path, auth) -> None:
    rt = RuntimeDir.init(tmp_path / "rt")
    client = TestClient(create_app(DaemonState.from_runtime(rt, Settings())))
    client.post(
        "/api/v1/assistant/configure",
        headers=auth,
        json={
            "selected_executor": "claude",
            "probe_results": [
                {"executor": "claude", "status": "passed", "command": "claude"}
            ],
        },
    )
    (rt.root / "system" / "assistant" / "workspace" / "CLAUDE.md").unlink()

    r = client.post("/api/v1/assistant/repair", headers=auth)

    assert r.status_code == 200
    assert r.json()["state"] == "configured"
    assert (rt.root / "system" / "assistant" / "workspace" / "CLAUDE.md").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/daemon/test_routes_assistant.py -v
```

Expected: FAIL with 404s for `/api/v1/assistant/status`.

- [ ] **Step 3: Implement assistant routes**

Create `runtime/daemon/routes/assistant.py`:

```python
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from runtime.daemon.assistant_pty import ProbeRunner, build_executor_specs
from runtime.daemon.auth import require_token
from runtime.daemon.state import DaemonState
from runtime.system_assistant import (
    AssistantConfig,
    bootstrap_assistant_workspace,
    classify_assistant_state,
    load_assistant_config,
    save_assistant_config,
    system_assistant_paths,
)

router = APIRouter(dependencies=[require_token()])


class ConfigureBody(BaseModel):
    selected_executor: str
    probe_results: list[dict[str, Any]]


def _runtime_root(state: DaemonState):
    if state.runtime is None:
        raise HTTPException(status_code=409, detail={"code": "no_active_runtime"})
    return state.runtime.root


@router.get("/assistant/status")
def status(request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    root = _runtime_root(state)
    return classify_assistant_state(root).model_dump()


@router.post("/assistant/probes")
def probes(request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    root = _runtime_root(state)
    specs = build_executor_specs(state.settings)
    runner = ProbeRunner()
    workspace_parent = system_assistant_paths(root).root
    workspace_parent.mkdir(parents=True, exist_ok=True)
    results = [
        runner.probe_executor(name, command=spec.command, workspace_parent=workspace_parent)
        for name, spec in specs.items()
    ]
    return {"results": results}


@router.post("/assistant/configure")
def configure(body: ConfigureBody, request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    root = _runtime_root(state)
    passed = [
        r for r in body.probe_results
        if r.get("executor") == body.selected_executor and r.get("status") == "passed"
    ]
    if not passed:
        raise HTTPException(
            status_code=400,
            detail={"code": "selected_executor_not_probe_passed"},
        )
    bootstrap_assistant_workspace(root, executor=body.selected_executor)
    paths = system_assistant_paths(root)
    selected_command = str(passed[0].get("command") or body.selected_executor)
    save_assistant_config(
        root,
        AssistantConfig(
            selected_executor=body.selected_executor,
            selected_command=selected_command,
            workspace_path=str(paths.workspace),
            latest_probe_results=body.probe_results,
        ),
    )
    return classify_assistant_state(root).model_dump()


@router.post("/assistant/repair")
def repair(request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    root = _runtime_root(state)
    config = load_assistant_config(root)
    if config is None:
        raise HTTPException(status_code=409, detail={"code": "assistant_uninitialized"})
    bootstrap_assistant_workspace(root, executor=config.selected_executor)
    return classify_assistant_state(root).model_dump()
```

Modify `runtime/daemon/app.py` imports and router registration:

```python
from runtime.daemon.routes import (
    agents,
    artifacts,
    assistant,
    audit,
    ...
)
```

and in `create_app`:

```python
app.include_router(assistant.router, prefix="/api/v1", tags=["assistant"])
```

- [ ] **Step 4: Run route tests**

Run:

```bash
uv run pytest tests/daemon/test_routes_assistant.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/daemon/routes/assistant.py runtime/daemon/app.py tests/daemon/test_routes_assistant.py
git commit -m "feat(assistant): add daemon setup routes"
```

## Task 4: CLI Assistant Init And Status

**Files:**
- Create: `cli/commands/assistant.py`
- Modify: `cli/main.py`
- Test: `tests/test_cli_assistant.py`

- [ ] **Step 1: Write failing CLI parser and command tests**

Create `tests/test_cli_assistant.py`:

```python
from __future__ import annotations

from unittest.mock import MagicMock, patch

from cli.main import build_parser


def test_assistant_bare_aliases_to_attach() -> None:
    parser = build_parser()
    args = parser.parse_args(["assistant"])

    assert args.command == "assistant"
    assert args.assistant_cmd == "attach"


def test_assistant_init_parser() -> None:
    parser = build_parser()
    args = parser.parse_args(["assistant", "init", "--reconfigure"])

    assert args.command == "assistant"
    assert args.assistant_cmd == "init"
    assert args.reconfigure is True


def test_cmd_assistant_status_prints_state(capsys) -> None:
    from cli.main import cmd_assistant_status

    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {
        "state": "configured",
        "selected_executor": "codex",
        "workspace_path": "/tmp/rt/system/assistant/workspace",
        "latest_probe_results": [],
    }

    with patch("cli.main.OpcClient.from_env", return_value=fake):
        cmd_assistant_status(MagicMock())

    fake.get.assert_called_once_with("/api/v1/assistant/status")
    out = capsys.readouterr().out
    assert "state: configured" in out
    assert "executor: codex" in out


def test_cmd_assistant_init_selects_only_passing_executor(monkeypatch, capsys) -> None:
    from cli.main import cmd_assistant_init

    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {"state": "uninitialized"}
    fake.post.side_effect = [
        MagicMock(
            status_code=200,
            json=lambda: {
                "results": [
                    {"executor": "claude", "status": "failed", "reason": "timeout", "hint": "login"},
                    {"executor": "codex", "status": "passed", "command": "codex"},
                ]
            },
        ),
        MagicMock(
            status_code=200,
            json=lambda: {"state": "configured", "selected_executor": "codex"},
        ),
    ]
    monkeypatch.setattr("builtins.input", lambda _: "1")

    with patch("cli.main.OpcClient.from_env", return_value=fake):
        args = MagicMock(repair=False, reconfigure=False)
        cmd_assistant_init(args)

    configure_call = fake.post.call_args_list[1]
    assert configure_call.args[0] == "/api/v1/assistant/configure"
    assert configure_call.kwargs["json"]["selected_executor"] == "codex"
    assert "1. codex" in capsys.readouterr().out
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_cli_assistant.py -v
```

Expected: FAIL because assistant CLI module and parser entries do not exist.

- [ ] **Step 3: Implement assistant CLI commands**

Create `cli/commands/assistant.py`:

```python
from __future__ import annotations

import argparse
import sys

from cli.client.client import DaemonNotRunning, DaemonStateInconsistent, OpcClient


def _client() -> OpcClient:
    try:
        return OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)


def _print_status(body: dict) -> None:
    print(f"state: {body['state']}")
    if body.get("selected_executor"):
        print(f"executor: {body['selected_executor']}")
    if body.get("workspace_path"):
        print(f"workspace: {body['workspace_path']}")
    if body.get("detail"):
        print(f"detail: {body['detail']}")


def cmd_assistant_status(args: argparse.Namespace) -> None:
    client = _client()
    r = client.get("/api/v1/assistant/status")
    if r.status_code != 200:
        print(f"Error ({r.status_code}): {r.text}")
        sys.exit(1)
    _print_status(r.json())


def _choose_executor(results: list[dict]) -> str:
    passing = [r for r in results if r.get("status") == "passed"]
    if not passing:
        print("No PTY-capable executor passed the HappyRanch probe.")
        for r in results:
            print(f"- {r.get('executor')}: {r.get('reason') or r.get('status')}")
            if r.get("hint"):
                print(f"  hint: {r['hint']}")
        sys.exit(2)
    print("PTY-capable executors:")
    for idx, result in enumerate(passing, start=1):
        print(f"{idx}. {result['executor']} ({result.get('command', result['executor'])})")
    while True:
        raw = input("Select executor: ").strip()
        try:
            selected = passing[int(raw) - 1]
        except (ValueError, IndexError):
            print(f"Enter a number from 1 to {len(passing)}.")
            continue
        return str(selected["executor"])


def cmd_assistant_init(args: argparse.Namespace) -> None:
    client = _client()
    status = client.get("/api/v1/assistant/status")
    if status.status_code != 200:
        print(f"Error ({status.status_code}): {status.text}")
        sys.exit(1)
    body = status.json()
    if body["state"] == "configured" and not args.reconfigure and not args.repair:
        _print_status(body)
        return
    if args.repair and not args.reconfigure:
        r = client.post("/api/v1/assistant/repair")
        if r.status_code != 200:
            print(f"Error ({r.status_code}): {r.text}")
            sys.exit(1)
        _print_status(r.json())
        return
    probes = client.post("/api/v1/assistant/probes")
    if probes.status_code != 200:
        print(f"Error ({probes.status_code}): {probes.text}")
        sys.exit(1)
    results = probes.json()["results"]
    selected = _choose_executor(results)
    configured = client.post(
        "/api/v1/assistant/configure",
        json={"selected_executor": selected, "probe_results": results},
    )
    if configured.status_code != 200:
        print(f"Error ({configured.status_code}): {configured.text}")
        sys.exit(1)
    _print_status(configured.json())


def cmd_assistant_attach(args: argparse.Namespace) -> None:
    print("assistant attach is not implemented yet; run `happyranch assistant status`.")
    sys.exit(2)


def register(sub) -> None:
    p = sub.add_parser("assistant", help="manage or attach to the system assistant")
    p.set_defaults(assistant_cmd="attach", func=cmd_assistant_attach)
    assistant_sub = p.add_subparsers(dest="assistant_cmd")
    assistant_sub.required = False

    p_init = assistant_sub.add_parser("init", help="initialize the system assistant")
    group = p_init.add_mutually_exclusive_group()
    group.add_argument("--repair", action="store_true")
    group.add_argument("--reconfigure", action="store_true")
    p_init.set_defaults(func=cmd_assistant_init)

    p_status = assistant_sub.add_parser("status", help="show system assistant status")
    p_status.set_defaults(func=cmd_assistant_status)

    p_attach = assistant_sub.add_parser("attach", help="attach to the system assistant")
    p_attach.set_defaults(func=cmd_assistant_attach)
```

Modify `cli/main.py`:

```python
from cli.commands import (
    agents,
    artifacts,
    assistant,
    jobs,
    ...
)

from cli.commands.assistant import (  # noqa: F401
    cmd_assistant_attach,
    cmd_assistant_init,
    cmd_assistant_status,
)
```

and in `build_parser()`:

```python
assistant.register(sub)
```

- [ ] **Step 4: Run CLI assistant tests**

Run:

```bash
uv run pytest tests/test_cli_assistant.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add cli/commands/assistant.py cli/main.py tests/test_cli_assistant.py
git commit -m "feat(cli): add assistant init and status commands"
```

## Task 5: Daemon-Owned PTY Session And WebSocket Attach

**Files:**
- Modify: `runtime/daemon/assistant_pty.py`
- Modify: `runtime/daemon/state.py`
- Modify: `runtime/daemon/routes/assistant.py`
- Test: `tests/daemon/test_routes_assistant.py`

- [ ] **Step 1: Add WebSocket attach test using a fake selected CLI**

Append to `tests/daemon/test_routes_assistant.py`:

```python
def _write_echo_cli(path: Path) -> str:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "print('assistant ready', flush=True)\n"
        "for line in sys.stdin:\n"
        "    print('echo:' + line.strip(), flush=True)\n"
    )
    path.chmod(path.stat().st_mode | 0o111)
    return str(path)


def test_assistant_websocket_streams_to_selected_cli(tmp_path: Path, auth) -> None:
    cli = _write_echo_cli(tmp_path / "echo_cli.py")
    rt = RuntimeDir.init(tmp_path / "rt")
    client = TestClient(create_app(DaemonState.from_runtime(rt, Settings())))
    client.post(
        "/api/v1/assistant/configure",
        headers=auth,
        json={
            "selected_executor": "codex",
            "probe_results": [
                {"executor": "codex", "status": "passed", "command": cli}
            ],
        },
    )

    token = auth["Authorization"].removeprefix("Bearer ")
    with client.websocket_connect(f"/api/v1/assistant/session?token={token}") as ws:
        first = ws.receive_text()
        assert "assistant ready" in first
        ws.send_text("hello\n")
        assert "echo:hello" in ws.receive_text()
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/daemon/test_routes_assistant.py::test_assistant_websocket_streams_to_selected_cli -v
```

Expected: FAIL because `/api/v1/assistant/session` is not implemented.

- [ ] **Step 3: Implement PTY session manager**

Extend `runtime/daemon/assistant_pty.py`:

```python
import asyncio
from collections.abc import AsyncIterator


class AssistantPtySession:
    def __init__(self, *, command: str, workspace: Path) -> None:
        self.command = command
        self.workspace = workspace
        self.master_fd: int | None = None
        self.proc: subprocess.Popen[bytes] | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._queue: asyncio.Queue[str] = asyncio.Queue()

    async def start(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            return
        master_fd, slave_fd = pty.openpty()
        self.master_fd = master_fd
        env = {
            "HOME": os.environ.get("HOME", ""),
            "PATH": os.environ.get("PATH", ""),
            "TERM": os.environ.get("TERM", "xterm-256color"),
        }
        self.proc = subprocess.Popen(
            [self.command],
            cwd=str(self.workspace),
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            env=env,
            start_new_session=True,
        )
        os.close(slave_fd)
        loop = asyncio.get_running_loop()
        self._reader_task = loop.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        assert self.master_fd is not None
        while self.proc is not None and self.proc.poll() is None:
            readable, _, _ = await asyncio.to_thread(select.select, [self.master_fd], [], [], 0.1)
            if not readable:
                continue
            try:
                chunk = os.read(self.master_fd, 4096).decode(errors="replace")
            except OSError:
                break
            if chunk:
                await self._queue.put(chunk)

    async def write(self, data: str) -> None:
        if self.master_fd is None:
            raise RuntimeError("PTY session is not started")
        os.write(self.master_fd, data.encode())

    async def output(self) -> AsyncIterator[str]:
        while True:
            yield await self._queue.get()

    async def close(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
        if self.proc is not None and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
        self.master_fd = None


class AssistantSessionManager:
    def __init__(self) -> None:
        self._session: AssistantPtySession | None = None
        self._attach_lock = asyncio.Lock()

    async def get_or_start(self, *, command: str, workspace: Path) -> AssistantPtySession:
        if self._session is None or self._session.proc is None or self._session.proc.poll() is not None:
            self._session = AssistantPtySession(command=command, workspace=workspace)
            await self._session.start()
        return self._session

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
```

Modify `runtime/daemon/state.py`:

```python
from runtime.daemon.assistant_pty import AssistantSessionManager
...
assistant_sessions: AssistantSessionManager = field(default_factory=AssistantSessionManager)
```

and in `close_all()` before clearing orgs:

```python
await self.assistant_sessions.close()
```

- [ ] **Step 4: Implement WebSocket route**

Extend `runtime/daemon/routes/assistant.py`:

```python
import asyncio

from fastapi import WebSocket, WebSocketDisconnect

from runtime.daemon import paths as daemon_paths
...


async def _require_ws_token(ws: WebSocket) -> None:
    token = ws.query_params.get("token")
    expected = daemon_paths.read_token()
    if token is None or expected is None or token != expected:
        await ws.close(code=4401)
        raise RuntimeError("unauthorized")


@router.websocket("/assistant/session")
async def assistant_session(ws: WebSocket) -> None:
    try:
        await _require_ws_token(ws)
    except RuntimeError:
        return
    await ws.accept()
    state: DaemonState = ws.app.state.daemon
    root = _runtime_root(state)
    status_body = classify_assistant_state(root)
    if status_body.state.value != "configured":
        await ws.send_text(f"assistant is {status_body.state.value}; run `happyranch assistant init`")
        await ws.close()
        return
    config = load_assistant_config(root)
    assert config is not None
    session = await state.assistant_sessions.get_or_start(
        command=config.selected_command,
        workspace=system_assistant_paths(root).workspace,
    )

    async def pump_output() -> None:
        async for chunk in session.output():
            await ws.send_text(chunk)

    output_task = asyncio.create_task(pump_output())
    try:
        while True:
            data = await ws.receive_text()
            await session.write(data)
    except WebSocketDisconnect:
        pass
    finally:
        output_task.cancel()
```

- [ ] **Step 5: Run WebSocket test**

Run:

```bash
uv run pytest tests/daemon/test_routes_assistant.py::test_assistant_websocket_streams_to_selected_cli -v
```

Expected: PASS.

- [ ] **Step 6: Run all assistant daemon route tests**

Run:

```bash
uv run pytest tests/daemon/test_routes_assistant.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add runtime/daemon/assistant_pty.py runtime/daemon/state.py runtime/daemon/routes/assistant.py tests/daemon/test_routes_assistant.py
git commit -m "feat(assistant): add PTY attach route"
```

## Task 6: CLI WebSocket Attach

**Files:**
- Modify: `pyproject.toml`
- Modify: `cli/commands/assistant.py`
- Test: `tests/test_cli_assistant.py`

- [ ] **Step 1: Write failing attach command tests for preflight states**

Append to `tests/test_cli_assistant.py`:

```python
def test_cmd_assistant_attach_uninitialized_prints_init_hint(capsys) -> None:
    from cli.main import cmd_assistant_attach

    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {"state": "uninitialized"}

    with patch("cli.main.OpcClient.from_env", return_value=fake):
        try:
            cmd_assistant_attach(MagicMock())
        except SystemExit as exc:
            assert exc.code == 2

    out = capsys.readouterr().out
    assert "happyranch assistant init" in out


def test_cmd_assistant_attach_configured_calls_bridge(monkeypatch) -> None:
    from cli.main import cmd_assistant_attach

    fake = MagicMock()
    fake.base_url = "http://127.0.0.1:8765"
    fake.headers = {"Authorization": "Bearer abc"}
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {"state": "configured"}
    called = {}

    def fake_bridge(client):
        called["base_url"] = client.base_url

    monkeypatch.setattr("cli.commands.assistant._run_attach_bridge", fake_bridge)
    with patch("cli.main.OpcClient.from_env", return_value=fake):
        cmd_assistant_attach(MagicMock())

    assert called == {"base_url": "http://127.0.0.1:8765"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_cli_assistant.py::test_cmd_assistant_attach_uninitialized_prints_init_hint tests/test_cli_assistant.py::test_cmd_assistant_attach_configured_calls_bridge -v
```

Expected: FAIL because `cmd_assistant_attach` is a placeholder.

- [ ] **Step 3: Add WebSocket dependency**

Modify `pyproject.toml` dependencies:

```toml
    "websockets>=12",
```

- [ ] **Step 4: Implement attach preflight and bridge**

Modify `cli/commands/assistant.py`:

```python
import asyncio
import sys
import termios
import tty
from urllib.parse import urlparse

import websockets
```

Replace `cmd_assistant_attach` and add helpers:

```python
def _ws_url(client: OpcClient) -> str:
    parsed = urlparse(client.base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    token = client.headers["Authorization"].removeprefix("Bearer ")
    return f"{scheme}://{parsed.netloc}/api/v1/assistant/session?token={token}"


async def _attach_async(client: OpcClient) -> None:
    async with websockets.connect(_ws_url(client)) as ws:
        old = termios.tcgetattr(sys.stdin.fileno())
        tty.setraw(sys.stdin.fileno())
        loop = asyncio.get_running_loop()
        try:
            async def read_stdin() -> None:
                while True:
                    data = await loop.run_in_executor(None, sys.stdin.read, 1)
                    await ws.send(data)

            async def write_stdout() -> None:
                async for msg in ws:
                    sys.stdout.write(msg)
                    sys.stdout.flush()

            await asyncio.gather(read_stdin(), write_stdout())
        finally:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old)


def _run_attach_bridge(client: OpcClient) -> None:
    asyncio.run(_attach_async(client))


def cmd_assistant_attach(args: argparse.Namespace) -> None:
    client = _client()
    r = client.get("/api/v1/assistant/status")
    if r.status_code != 200:
        print(f"Error ({r.status_code}): {r.text}")
        sys.exit(1)
    status = r.json()
    if status["state"] == "uninitialized":
        print("System assistant is not initialized. Run `happyranch assistant init`.")
        sys.exit(2)
    if status["state"] != "configured":
        print("System assistant is not healthy. Run `happyranch assistant init --repair` or `--reconfigure`.")
        sys.exit(2)
    _run_attach_bridge(client)
```

- [ ] **Step 5: Run CLI attach tests**

Run:

```bash
uv run pytest tests/test_cli_assistant.py -v
```

Expected: PASS.

- [ ] **Step 6: Run dependency lock update if needed**

Run:

```bash
uv lock
```

Expected: lockfile updates only if `websockets` was not already locked transitively.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock cli/commands/assistant.py tests/test_cli_assistant.py
git commit -m "feat(cli): attach to system assistant PTY"
```

## Task 7: Contract, Docs, And Full Verification

**Files:**
- Modify: `docs/agent-guides/runtime-and-configuration.md`
- Modify: `docs/agent-guides/web-and-cli.md`
- Modify: `README.md`
- Test: existing suites

- [ ] **Step 1: Document system assistant setup and existing runtime upgrade**

Add to `docs/agent-guides/runtime-and-configuration.md` under runtime setup:

```md
## System Assistant

The system assistant is runtime-global and lives under
`<runtime>/system/assistant/`. It is not an org agent and must not appear in
`org/agents/` or `teams.yaml`.

Initialize or repair it on the active runtime:

```bash
happyranch assistant init
happyranch assistant init --repair
happyranch assistant init --reconfigure
```

Initialization probes supported agentic CLIs through a long-lived interactive
PTY session. A CLI passes only when it replies to `HAPPYRANCH_PTY_PROBE_V1`
with `HAPPYRANCH_PTY_READY_V1` before timeout. Existing runtimes that predate
the feature remain valid; `happyranch assistant` tells the user to run
`happyranch assistant init` when no assistant config exists.
```

- [ ] **Step 2: Document CLI surface**

Add to `docs/agent-guides/web-and-cli.md` under CLI:

```md
System assistant commands are container-level:

```bash
happyranch assistant init [--repair|--reconfigure]
happyranch assistant status
happyranch assistant
```

`happyranch assistant` attaches to the daemon-owned PTY for the runtime-global
system assistant. It does not take `--org`.
```

- [ ] **Step 3: Update README quick start**

Add after `happyranch init ~/happyranch-runtime` in `README.md`:

```md
# Optional but recommended: initialize the runtime-global system assistant.
# This also verifies that at least one supported agentic CLI works in an
# interactive PTY session.
happyranch assistant init
happyranch assistant
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
uv run pytest tests/test_system_assistant.py tests/test_assistant_pty.py tests/daemon/test_routes_assistant.py tests/test_cli_assistant.py -v
```

Expected: PASS.

- [ ] **Step 5: Run broader unit suite**

Run:

```bash
uv run pytest tests/ -v
```

Expected: PASS.

- [ ] **Step 6: Run OpenAPI snapshot check**

Run:

```bash
uv run pytest tests/contract/test_openapi_snapshot.py -v
```

Expected: FAIL if new assistant HTTP routes change OpenAPI. If it fails only due to intentional assistant routes, regenerate:

```bash
HAPPYRANCH_REGEN_OPENAPI=1 uv run pytest tests/contract/test_openapi_snapshot.py -v
```

Then rerun:

```bash
uv run pytest tests/contract/test_openapi_snapshot.py -v
```

Expected: PASS.

- [ ] **Step 7: Run GitNexus detect changes**

If the MCP tool is available, run `gitnexus_detect_changes()` and confirm the affected scope is assistant runtime/CLI/routes only. If the MCP tool is unavailable, run:

```bash
npx gitnexus status
```

Expected: `Status: ✅ up-to-date`. Note the missing MCP tool in the final handoff.

- [ ] **Step 8: Commit docs and snapshot updates**

```bash
git add README.md docs/agent-guides/runtime-and-configuration.md docs/agent-guides/web-and-cli.md tests/contract/openapi.json
git commit -m "docs(assistant): document system assistant setup"
```

## Self-Review

- Spec coverage:
  - Runtime-global identity: Task 1 and Task 3.
  - Fixed PTY request/reply probe: Task 2 and Task 4.
  - Existing runtime upgrade path: Task 3, Task 4, Task 7.
  - CLI interaction: Task 4 and Task 6.
  - Daemon-owned PTY with single runtime-derived cwd: Task 5.
  - Diagnostic/guided authority: Task 1 bootstrap prompt and Task 7 docs.
  - Web/xterm future compatibility: Task 5 uses WebSocket backend.
- Red-flag scan: no task leaves unspecified implementation or test work.
- Type consistency:
  - `AssistantState`, `AssistantConfig`, and `AssistantStatus` are defined in Task 1 and reused consistently.
  - Route paths are consistently `/api/v1/assistant/...`.
  - CLI command family uses `assistant_cmd` consistently.
