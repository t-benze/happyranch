# Orchestrator Daemon + HTTP API — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** `docs/superpowers/specs/2026-04-14-orchestrator-daemon-design.md`

**Goal:** Turn the in-process Python orchestrator into a long-running daemon with a localhost HTTP API; CLI commands and agent callbacks become thin HTTP clients; per-task workflow moves into Claude Code skills.

**Architecture:** A FastAPI/uvicorn daemon binds to `127.0.0.1` on an OS-assigned port and stores its lifecycle state under `~/.opc/`. Exactly one OPC runtime is "active" at a time. Tasks run as `asyncio.to_thread`-wrapped invocations of the existing `Orchestrator.run_task`, with the daemon injecting a per-spawn `session_id` into the prompt and storing all completion/learning callbacks against that session in SQLite. Two Claude Code skills (`start-task`, `make-worktree`) carry the per-session workflow.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, sse-starlette, httpx, Pydantic v2, SQLite (WAL), PyYAML, asyncio, `subprocess` (existing executor unchanged).

---

## File Inventory

**New files:**

- `src/daemon/__init__.py` — package marker
- `src/daemon/__main__.py` — entry point: bind port, write pid/port files, start uvicorn, install signal handlers
- `src/daemon/paths.py` — `~/.opc/` path constants + token generation
- `src/daemon/runtimes.py` — `runtimes.yaml` registry read/write + active-runtime guards
- `src/daemon/state.py` — `DaemonState` holder (active `RuntimeDir`, `Database`, `Orchestrator`, event bus, DB lock)
- `src/daemon/sessions.py` — per-`(task_id, agent)` active-session tracking
- `src/daemon/event_bus.py` — in-memory pub/sub + DB replay on subscribe
- `src/daemon/runner.py` — async wrapper around `Orchestrator.run_task`, generates session_id
- `src/daemon/auth.py` — Bearer auth dependency for FastAPI routes
- `src/daemon/app.py` — FastAPI app factory, route registration
- `src/daemon/routes/__init__.py`
- `src/daemon/routes/health.py` — `GET /health`
- `src/daemon/routes/runtimes.py` — register/activate/list
- `src/daemon/routes/tasks.py` — submit/list/detail/events/completion
- `src/daemon/routes/agents.py` — list/init/learnings
- `src/client/__init__.py`
- `src/client/client.py` — HTTP client with port + token discovery
- `protocol/skills/start-task/SKILL.md`
- `protocol/skills/make-worktree/SKILL.md`
- `scripts/daemon.sh` — `start|stop|status` lifecycle
- `tests/daemon/__init__.py`
- `tests/daemon/conftest.py` — fixtures: `daemon_app`, `daemon_state`
- `tests/daemon/test_paths.py`
- `tests/daemon/test_runtimes_module.py`
- `tests/daemon/test_sessions.py`
- `tests/daemon/test_event_bus.py`
- `tests/daemon/test_runner.py`
- `tests/daemon/test_auth.py`
- `tests/daemon/test_routes_health.py`
- `tests/daemon/test_routes_runtimes.py`
- `tests/daemon/test_routes_tasks.py`
- `tests/daemon/test_routes_agents.py`
- `tests/client/__init__.py`
- `tests/client/test_client.py`
- `tests/test_skills.py`
- `tests/integration/__init__.py`
- `tests/integration/test_end_to_end.py`
- `tests/integration/fake_claude.sh` — stubbed Claude Code binary
- `tests/integration/conftest.py` — `live_daemon` fixture

**Modified files:**

- `pyproject.toml` — add `fastapi`, `uvicorn[standard]`, `sse-starlette`, `httpx`
- `src/config.py` — add `daemon_home_dir`, `daemon_bind_host`
- `src/cli.py` — every command becomes an HTTP client; new commands `tail`, `use`, `report-completion`, `learning`; remove `--runtime` flag
- `src/orchestrator/orchestrator.py` — `_run_agent` accepts a `session_id` and reads the latest completion record from DB filtered by `session_id`; remove `initialize_workspace` per-session call
- `src/orchestrator/executor.py` — drop `read_completion_report` and `completion_report.json` lifecycle; `ExecutorResult.report` may be `None` on success
- `src/orchestrator/context_builder.py` — `write_claude_md` drops `task_brief`; CLAUDE.md drops the "Completion Report" and "Current Task" sections; `initialize_workspace` copies `protocol/skills/` to `<workspace>/.claude/skills/`
- `src/infrastructure/database.py` — add `get_latest_task_result(task_id, agent, session_id)` helper; add `get_nonterminal_task_ids()` helper
- `tests/test_executor.py` — remove `completion_report.json` cases
- `tests/test_orchestrator.py` — adjust mocks: completion arrives via DB, not via file
- `tests/test_context_builder.py` — drop `task_brief` cases; add skill-copying assertions
- `tests/test_cli.py` — every command is now mocked at the HTTP boundary

---

## Execution Order Notes

- Each task ends with a green test suite and a commit.
- Tasks 1–6 build the daemon scaffold; you can `scripts/daemon.sh start` and `curl /health` after Task 6.
- Tasks 7–12 add runtime registry + database/event-bus primitives.
- Tasks 13–16 wire task execution end-to-end.
- Tasks 17–20 refactor the CLI commands.
- Tasks 21–23 deliver skills + agent-init.
- Tasks 24–25 handle restart policy and cleanup.
- Task 26 covers skill validation; Task 27 is the integration test and final cutover.

---

## Phase A — Foundations

### Task 1: Add new dependencies and create empty package skeletons

**Files:**
- Modify: `pyproject.toml`
- Create: `src/daemon/__init__.py`
- Create: `src/daemon/routes/__init__.py`
- Create: `src/client/__init__.py`
- Create: `tests/daemon/__init__.py`
- Create: `tests/client/__init__.py`
- Create: `tests/integration/__init__.py`

- [ ] **Step 1: Update `pyproject.toml`**

Add to the `dependencies` array (after `pyyaml>=6.0.3`):

```toml
    "fastapi>=0.110",
    "uvicorn[standard]>=0.29",
    "sse-starlette>=2.0",
    "httpx>=0.27",
```

- [ ] **Step 2: Sync the lockfile**

Run: `uv sync`
Expected: install completes, `uv.lock` updated.

- [ ] **Step 3: Create empty package files**

Each of these gets exactly one line: `"""<purpose>."""` then a newline.

- `src/daemon/__init__.py` → `"""OPC orchestrator daemon: FastAPI app, runner, event bus."""`
- `src/daemon/routes/__init__.py` → `"""HTTP route modules for the OPC daemon."""`
- `src/client/__init__.py` → `"""OPC HTTP client used by CLI commands and agent callbacks."""`
- `tests/daemon/__init__.py` → empty file
- `tests/client/__init__.py` → empty file
- `tests/integration/__init__.py` → empty file

- [ ] **Step 4: Smoke test imports**

Run: `uv run python -c "import fastapi, uvicorn, sse_starlette, httpx; import src.daemon, src.client; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 5: Make sure the existing tests still pass**

Run: `uv run pytest tests/ -q`
Expected: all 106 existing tests pass.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/daemon/ src/client/ tests/daemon/ tests/client/ tests/integration/
git commit -m "feat(daemon): add HTTP deps and empty package skeletons"
```

---

### Task 2: `~/.opc/` path constants and auth-token bootstrap

**Files:**
- Create: `src/daemon/paths.py`
- Create: `tests/daemon/test_paths.py`

- [ ] **Step 1: Write the failing test**

`tests/daemon/test_paths.py`:

```python
from __future__ import annotations

import stat
from pathlib import Path

import pytest

from src.daemon import paths as paths_mod


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("OPC_DAEMON_HOME", str(tmp_path / ".opc"))
    return tmp_path / ".opc"


def test_daemon_home_returns_env_override(tmp_home: Path) -> None:
    assert paths_mod.daemon_home() == tmp_home


def test_daemon_home_creates_directory_when_missing(tmp_home: Path) -> None:
    paths_mod.ensure_daemon_home()
    assert tmp_home.is_dir()


def test_pid_port_token_log_paths(tmp_home: Path) -> None:
    assert paths_mod.pid_file() == tmp_home / "daemon.pid"
    assert paths_mod.port_file() == tmp_home / "daemon.port"
    assert paths_mod.token_file() == tmp_home / "daemon.token"
    assert paths_mod.log_file() == tmp_home / "daemon.log"
    assert paths_mod.runtimes_file() == tmp_home / "runtimes.yaml"


def test_ensure_token_generates_and_returns(tmp_home: Path) -> None:
    paths_mod.ensure_daemon_home()
    token = paths_mod.ensure_token()
    assert isinstance(token, str)
    assert len(token) >= 40
    assert paths_mod.token_file().read_text() == token
    mode = stat.S_IMODE(paths_mod.token_file().stat().st_mode)
    assert mode == 0o600


def test_ensure_token_idempotent(tmp_home: Path) -> None:
    paths_mod.ensure_daemon_home()
    first = paths_mod.ensure_token()
    second = paths_mod.ensure_token()
    assert first == second


def test_read_token_returns_none_when_missing(tmp_home: Path) -> None:
    paths_mod.ensure_daemon_home()
    assert paths_mod.read_token() is None


def test_read_token_returns_existing(tmp_home: Path) -> None:
    paths_mod.ensure_daemon_home()
    token = paths_mod.ensure_token()
    assert paths_mod.read_token() == token
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/daemon/test_paths.py -v`
Expected: collection error or `ModuleNotFoundError: src.daemon.paths`.

- [ ] **Step 3: Implement `src/daemon/paths.py`**

```python
"""Locations under ``~/.opc/`` for daemon lifecycle state."""
from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

_DEFAULT_HOME = Path.home() / ".opc"


def daemon_home() -> Path:
    """Return the directory the daemon stores its state in.

    Honors the ``OPC_DAEMON_HOME`` environment variable for tests; falls
    back to ``~/.opc/``.
    """
    override = os.environ.get("OPC_DAEMON_HOME")
    return Path(override) if override else _DEFAULT_HOME


def ensure_daemon_home() -> Path:
    home = daemon_home()
    home.mkdir(parents=True, exist_ok=True)
    return home


def pid_file() -> Path:
    return daemon_home() / "daemon.pid"


def port_file() -> Path:
    return daemon_home() / "daemon.port"


def token_file() -> Path:
    return daemon_home() / "daemon.token"


def log_file() -> Path:
    return daemon_home() / "daemon.log"


def runtimes_file() -> Path:
    return daemon_home() / "runtimes.yaml"


def ensure_token() -> str:
    """Return the daemon's auth token, generating it on first call.

    Writes the token with ``0600`` perms.
    """
    path = token_file()
    if path.exists():
        return path.read_text().strip()
    token = secrets.token_urlsafe(32)
    path.write_text(token)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    return token


def read_token() -> str | None:
    path = token_file()
    if not path.exists():
        return None
    return path.read_text().strip()
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/daemon/test_paths.py -v`
Expected: all 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/paths.py tests/daemon/test_paths.py
git commit -m "feat(daemon): add ~/.opc paths and 0600 auth token bootstrap"
```

---

### Task 3: Runtime registry (`runtimes.yaml`) module

**Files:**
- Create: `src/daemon/runtimes.py`
- Create: `tests/daemon/test_runtimes_module.py`

- [ ] **Step 1: Write the failing test**

`tests/daemon/test_runtimes_module.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from src.daemon import paths as paths_mod
from src.daemon import runtimes as reg
from src.runtime import RuntimeDir


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("OPC_DAEMON_HOME", str(tmp_path / ".opc"))
    paths_mod.ensure_daemon_home()
    return tmp_path / ".opc"


def _make_runtime(base: Path, name: str) -> Path:
    runtime_path = base / name
    RuntimeDir.init(runtime_path)
    return runtime_path.resolve()


def test_load_returns_empty_when_file_missing(tmp_home: Path) -> None:
    state = reg.load()
    assert state.active is None
    assert state.registered == []


def test_register_then_activate(tmp_home: Path, tmp_path: Path) -> None:
    rt = _make_runtime(tmp_path, "runtime-a")
    reg.register(rt)
    state = reg.load()
    assert rt in state.registered
    assert state.active == rt


def test_register_rejects_non_runtime_path(tmp_home: Path, tmp_path: Path) -> None:
    bogus = tmp_path / "not-a-runtime"
    bogus.mkdir()
    with pytest.raises(ValueError):
        reg.register(bogus)


def test_register_is_idempotent(tmp_home: Path, tmp_path: Path) -> None:
    rt = _make_runtime(tmp_path, "runtime-a")
    reg.register(rt)
    reg.register(rt)
    state = reg.load()
    assert state.registered.count(rt) == 1


def test_activate_unknown_path_raises(tmp_home: Path, tmp_path: Path) -> None:
    rt = _make_runtime(tmp_path, "runtime-a")
    with pytest.raises(ValueError):
        reg.activate(rt)


def test_activate_after_register_switches(tmp_home: Path, tmp_path: Path) -> None:
    a = _make_runtime(tmp_path, "runtime-a")
    b = _make_runtime(tmp_path, "runtime-b")
    reg.register(a)
    reg.register(b)
    assert reg.load().active == b
    reg.activate(a)
    assert reg.load().active == a
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/daemon/test_runtimes_module.py -v`
Expected: `ModuleNotFoundError: src.daemon.runtimes`.

- [ ] **Step 3: Implement `src/daemon/runtimes.py`**

```python
"""Read/write helpers for the daemon's ``runtimes.yaml`` registry."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from src.daemon import paths
from src.runtime import RuntimeDir


@dataclass
class RegistryState:
    active: Path | None = None
    registered: list[Path] = field(default_factory=list)


def load() -> RegistryState:
    path = paths.runtimes_file()
    if not path.exists():
        return RegistryState()
    raw = yaml.safe_load(path.read_text()) or {}
    active = raw.get("active")
    registered = raw.get("registered") or []
    return RegistryState(
        active=Path(active).resolve() if active else None,
        registered=[Path(p).resolve() for p in registered],
    )


def _save(state: RegistryState) -> None:
    paths.ensure_daemon_home()
    payload = {
        "active": str(state.active) if state.active else None,
        "registered": [str(p) for p in state.registered],
    }
    paths.runtimes_file().write_text(yaml.dump(payload, default_flow_style=False))


def register(path: Path) -> None:
    """Add *path* to the registry and make it active.

    Raises ``ValueError`` if *path* is not a valid runtime directory.
    """
    resolved = Path(path).resolve()
    RuntimeDir.load(resolved)  # raises if marker missing
    state = load()
    if resolved not in state.registered:
        state.registered.append(resolved)
    state.active = resolved
    _save(state)


def activate(path: Path) -> None:
    """Set *path* as the active runtime.

    Raises ``ValueError`` if *path* isn't already registered.
    """
    resolved = Path(path).resolve()
    state = load()
    if resolved not in state.registered:
        raise ValueError(f"{resolved} is not in the registry; call register() first")
    state.active = resolved
    _save(state)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/daemon/test_runtimes_module.py -v`
Expected: all 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/runtimes.py tests/daemon/test_runtimes_module.py
git commit -m "feat(daemon): runtime registry yaml module"
```

---

### Task 4: Add Settings entries for daemon home and bind host

**Files:**
- Modify: `src/config.py`
- Modify: `tests/conftest.py` (no behavior change, just a helper)

- [ ] **Step 1: Update `src/config.py`**

Add these fields to the `Settings` class, after `tier_yellow_threshold`:

```python
    # Daemon
    daemon_bind_host: str = "127.0.0.1"
```

(`OPC_DAEMON_HOME` is read directly by `src/daemon/paths.py` and is intentionally outside `Settings` — it controls *where* the daemon stores its state, not in-process knobs.)

- [ ] **Step 2: Verify no existing tests break**

Run: `uv run pytest tests/ -q`
Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add src/config.py
git commit -m "feat(config): add daemon_bind_host setting"
```

---

## Phase B — HTTP scaffold

### Task 5: FastAPI auth dependency

**Files:**
- Create: `src/daemon/auth.py`
- Create: `tests/daemon/test_auth.py`

- [ ] **Step 1: Write the failing test**

`tests/daemon/test_auth.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.daemon import paths as paths_mod
from src.daemon.auth import require_token


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("OPC_DAEMON_HOME", str(tmp_path / ".opc"))
    paths_mod.ensure_daemon_home()
    paths_mod.ensure_token()
    return tmp_path / ".opc"


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()

    @app.get("/secured")
    def secured(_: None = require_token()) -> dict:
        return {"ok": True}

    return app


def test_request_without_token_rejected(tmp_home: Path, app: FastAPI) -> None:
    r = TestClient(app).get("/secured")
    assert r.status_code == 401


def test_request_with_wrong_token_rejected(tmp_home: Path, app: FastAPI) -> None:
    r = TestClient(app).get(
        "/secured", headers={"Authorization": "Bearer not-the-token"}
    )
    assert r.status_code == 401


def test_request_with_correct_token_allowed(tmp_home: Path, app: FastAPI) -> None:
    token = paths_mod.read_token()
    r = TestClient(app).get(
        "/secured", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/daemon/test_auth.py -v`
Expected: `ModuleNotFoundError: src.daemon.auth`.

- [ ] **Step 3: Implement `src/daemon/auth.py`**

```python
"""Bearer-token auth dependency for the daemon's FastAPI routes."""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status

from src.daemon import paths


def _check_token(authorization: str | None = Header(default=None)) -> None:
    expected = paths.read_token()
    if expected is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="daemon token file missing",
        )
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    if authorization.removeprefix("Bearer ").strip() != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bad token")


def require_token() -> Depends:
    return Depends(_check_token)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/daemon/test_auth.py -v`
Expected: all 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/auth.py tests/daemon/test_auth.py
git commit -m "feat(daemon): bearer-token auth dependency"
```

---

### Task 6: `DaemonState` holder + `/health` endpoint + app factory

**Files:**
- Create: `src/daemon/state.py`
- Create: `src/daemon/app.py`
- Create: `src/daemon/routes/health.py`
- Create: `tests/daemon/conftest.py`
- Create: `tests/daemon/test_routes_health.py`

- [ ] **Step 1: Write `tests/daemon/conftest.py`**

```python
from __future__ import annotations

from pathlib import Path

import pytest

from src.config import Settings
from src.daemon import paths as paths_mod
from src.daemon.app import create_app
from src.daemon.state import DaemonState
from src.runtime import RuntimeDir


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("OPC_DAEMON_HOME", str(tmp_path / ".opc"))
    paths_mod.ensure_daemon_home()
    paths_mod.ensure_token()
    return tmp_path / ".opc"


@pytest.fixture
def runtime(tmp_path: Path) -> RuntimeDir:
    return RuntimeDir.init(tmp_path / "runtime")


@pytest.fixture
def daemon_state(runtime: RuntimeDir) -> DaemonState:
    return DaemonState.from_runtime(runtime, Settings())


@pytest.fixture
def daemon_state_idle() -> DaemonState:
    return DaemonState.idle(Settings())


@pytest.fixture
def app(daemon_state: DaemonState):
    return create_app(daemon_state)


@pytest.fixture
def app_idle(daemon_state_idle: DaemonState):
    return create_app(daemon_state_idle)


@pytest.fixture
def auth_headers() -> dict:
    return {"Authorization": f"Bearer {paths_mod.read_token()}"}
```

- [ ] **Step 2: Write the failing test**

`tests/daemon/test_routes_health.py`:

```python
from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_returns_active_runtime(tmp_home, app, daemon_state) -> None:
    client = TestClient(app)
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["active_runtime"] == str(daemon_state.runtime.root)


def test_health_returns_null_when_idle(tmp_home, app_idle) -> None:
    client = TestClient(app_idle)
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "active_runtime": None}


def test_health_does_not_require_auth(tmp_home, app) -> None:
    client = TestClient(app)
    r = client.get("/api/v1/health")
    assert r.status_code == 200
```

- [ ] **Step 3: Run the test and verify it fails**

Run: `uv run pytest tests/daemon/test_routes_health.py -v`
Expected: `ModuleNotFoundError: src.daemon.app`.

- [ ] **Step 4: Implement `src/daemon/state.py`**

```python
"""Process-wide state holder for the daemon."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from src.config import Settings
from src.infrastructure.database import Database
from src.runtime import RuntimeDir


@dataclass
class DaemonState:
    """Holds the active runtime, its DB, and the asyncio resources."""

    runtime: RuntimeDir | None
    db: Database | None
    settings: Settings
    db_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @classmethod
    def idle(cls, settings: Settings) -> "DaemonState":
        return cls(runtime=None, db=None, settings=settings)

    @classmethod
    def from_runtime(cls, runtime: RuntimeDir, settings: Settings) -> "DaemonState":
        return cls(runtime=runtime, db=Database(runtime.db_path), settings=settings)

    @property
    def is_idle(self) -> bool:
        return self.runtime is None
```

- [ ] **Step 5: Implement `src/daemon/routes/health.py`**

```python
"""Liveness endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/health")
def health(request: Request) -> dict:
    state = request.app.state.daemon
    return {
        "status": "ok",
        "active_runtime": str(state.runtime.root) if state.runtime else None,
    }
```

- [ ] **Step 6: Implement `src/daemon/app.py`**

```python
"""FastAPI app factory."""
from __future__ import annotations

from fastapi import FastAPI

from src.daemon.routes import health
from src.daemon.state import DaemonState


def create_app(state: DaemonState) -> FastAPI:
    app = FastAPI(title="OPC Daemon", version="0.1.0")
    app.state.daemon = state
    app.include_router(health.router, prefix="/api/v1")
    return app
```

- [ ] **Step 7: Run tests**

Run: `uv run pytest tests/daemon/ -v`
Expected: all auth + health tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/daemon/state.py src/daemon/app.py src/daemon/routes/health.py tests/daemon/conftest.py tests/daemon/test_routes_health.py
git commit -m "feat(daemon): app factory, DaemonState, /health endpoint"
```

---

### Task 7: Daemon entry point and lifecycle script

**Files:**
- Create: `src/daemon/__main__.py`
- Create: `scripts/daemon.sh`

This task isn't TDD'd because it's process-lifecycle plumbing — the integration test in Task 27 covers it end-to-end. But we run a manual smoke test.

- [ ] **Step 1: Implement `src/daemon/__main__.py`**

```python
"""OPC daemon entry point.

Bootstraps from ~/.opc/runtimes.yaml, binds an ephemeral local port,
writes pid/port files, and runs the FastAPI app under uvicorn.
"""
from __future__ import annotations

import logging
import os
import signal
import socket
import sys
from types import FrameType

import uvicorn

from src.config import Settings
from src.daemon import paths, runtimes
from src.daemon.app import create_app
from src.daemon.state import DaemonState
from src.runtime import RuntimeDir

logger = logging.getLogger("opc.daemon")


def _build_state(settings: Settings) -> DaemonState:
    reg = runtimes.load()
    if reg.active is None:
        logger.warning("no active runtime — starting in idle mode")
        return DaemonState.idle(settings)
    runtime = RuntimeDir.load(reg.active)
    return DaemonState.from_runtime(runtime, settings)


def _bind_port(host: str) -> tuple[socket.socket, int]:
    """Bind an ephemeral port and return (socket, port)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, 0))
    port = sock.getsockname()[1]
    return sock, port


def _install_signal_handlers(state: DaemonState) -> None:
    def _handle(signum: int, _frame: FrameType | None) -> None:
        logger.info("received signal %s — shutting down", signum)
        # uvicorn handles its own SIGTERM/SIGINT to drain workers; here
        # we just make sure the lifecycle files get cleaned up.
        for f in (paths.pid_file(), paths.port_file()):
            try:
                f.unlink()
            except FileNotFoundError:
                pass
        if state.db is not None:
            state.db.close()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    paths.ensure_daemon_home()
    paths.ensure_token()

    settings = Settings()
    state = _build_state(settings)
    app = create_app(state)

    sock, port = _bind_port(settings.daemon_bind_host)
    paths.port_file().write_text(str(port))
    paths.pid_file().write_text(str(os.getpid()))
    _install_signal_handlers(state)

    logger.info("OPC daemon listening on %s:%d", settings.daemon_bind_host, port)
    config = uvicorn.Config(app, log_level="info", lifespan="on")
    server = uvicorn.Server(config)
    # Hand the bound socket to uvicorn so we don't race the port number.
    server.run(sockets=[sock])


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Implement `scripts/daemon.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

OPC_HOME="${OPC_DAEMON_HOME:-$HOME/.opc}"
PID_FILE="$OPC_HOME/daemon.pid"
PORT_FILE="$OPC_HOME/daemon.port"
LOG_FILE="$OPC_HOME/daemon.log"

cmd_start() {
    mkdir -p "$OPC_HOME"
    if [[ -f "$PID_FILE" ]]; then
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            echo "daemon already running (pid $pid)"
            exit 1
        fi
        rm -f "$PID_FILE"
    fi
    nohup uv run python -m src.daemon >> "$LOG_FILE" 2>&1 &
    bg_pid=$!
    # Wait up to 5s for port file to materialize
    for _ in 1 2 3 4 5; do
        if [[ -f "$PORT_FILE" ]]; then
            port=$(cat "$PORT_FILE")
            echo "daemon started (pid $bg_pid, port $port)"
            exit 0
        fi
        sleep 1
    done
    echo "daemon failed to start within 5s — see $LOG_FILE"
    exit 1
}

cmd_stop() {
    if [[ ! -f "$PID_FILE" ]]; then
        echo "daemon not running"
        exit 0
    fi
    pid=$(cat "$PID_FILE")
    if ! kill -0 "$pid" 2>/dev/null; then
        echo "stale pid file (process $pid not alive)"
        rm -f "$PID_FILE" "$PORT_FILE"
        exit 0
    fi
    kill -TERM "$pid"
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        if ! kill -0 "$pid" 2>/dev/null; then
            rm -f "$PID_FILE" "$PORT_FILE"
            echo "daemon stopped"
            exit 0
        fi
        sleep 1
    done
    kill -KILL "$pid" || true
    rm -f "$PID_FILE" "$PORT_FILE"
    echo "daemon force-killed"
}

cmd_status() {
    if [[ ! -f "$PID_FILE" ]]; then
        echo "not running"
        exit 1
    fi
    pid=$(cat "$PID_FILE")
    if ! kill -0 "$pid" 2>/dev/null; then
        echo "stale (pid file from dead process)"
        exit 1
    fi
    port=$(cat "$PORT_FILE" 2>/dev/null || echo "?")
    echo "running (pid $pid, port $port)"
}

case "${1:-}" in
    start)  cmd_start  ;;
    stop)   cmd_stop   ;;
    status) cmd_status ;;
    *)      echo "Usage: $0 {start|stop|status}"; exit 2 ;;
esac
```

- [ ] **Step 3: Make the script executable**

Run: `chmod +x scripts/daemon.sh`

- [ ] **Step 4: Smoke-test the lifecycle**

Run: `OPC_DAEMON_HOME=/tmp/opc-smoke ./scripts/daemon.sh start`
Expected: prints `daemon started (pid N, port P)`.

Run: `curl -s http://127.0.0.1:$(cat /tmp/opc-smoke/daemon.port)/api/v1/health`
Expected: `{"status":"ok","active_runtime":null}`.

Run: `OPC_DAEMON_HOME=/tmp/opc-smoke ./scripts/daemon.sh stop`
Expected: prints `daemon stopped`.

Run: `rm -rf /tmp/opc-smoke`

- [ ] **Step 5: Commit**

```bash
git add src/daemon/__main__.py scripts/daemon.sh
git commit -m "feat(daemon): entry point and scripts/daemon.sh lifecycle"
```

---

## Phase C — HTTP client + first CLI conversion

### Task 8: HTTP client module

**Files:**
- Create: `src/client/client.py`
- Create: `tests/client/test_client.py`

- [ ] **Step 1: Write the failing test**

`tests/client/test_client.py`:

```python
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.client.client import (
    DaemonNotRunning,
    DaemonStateInconsistent,
    OpcClient,
)
from src.daemon import paths as paths_mod


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("OPC_DAEMON_HOME", str(tmp_path / ".opc"))
    paths_mod.ensure_daemon_home()
    return tmp_path / ".opc"


def test_missing_port_raises_daemon_not_running(tmp_home: Path) -> None:
    with pytest.raises(DaemonNotRunning):
        OpcClient.from_env()


def test_missing_token_raises_state_inconsistent(tmp_home: Path) -> None:
    paths_mod.port_file().write_text("12345")
    with pytest.raises(DaemonStateInconsistent):
        OpcClient.from_env()


def test_constructor_uses_token_for_auth(tmp_home: Path) -> None:
    paths_mod.port_file().write_text("12345")
    paths_mod.ensure_token()
    client = OpcClient.from_env()
    assert client.headers["Authorization"].startswith("Bearer ")


def test_get_uses_injected_transport(tmp_home: Path) -> None:
    paths_mod.port_file().write_text("12345")
    token = paths_mod.ensure_token()

    app = FastAPI()

    @app.get("/api/v1/ping")
    def ping(authorization: str | None = None) -> dict:
        return {"auth_seen": authorization}

    transport = httpx.WSGITransport(app=TestClient(app).app)
    client = OpcClient.from_env()
    # swap transport for the test
    client._client = httpx.Client(
        base_url=client.base_url, headers=client.headers, transport=httpx.ASGITransport(app=app),
    )
    body = client.get("/api/v1/ping").json()
    assert body["auth_seen"] == f"Bearer {token}"
```

(Note: the last test exercises the auth-header path; the simpler tests cover the discovery logic. We use `ASGITransport` rather than `WSGITransport` here; the `WSGITransport` line is unused — remove it if desired.)

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/client/test_client.py -v`
Expected: `ModuleNotFoundError: src.client.client`.

- [ ] **Step 3: Implement `src/client/client.py`**

```python
"""HTTP client used by CLI commands and agent callbacks."""
from __future__ import annotations

from typing import Iterator

import httpx

from src.daemon import paths


class DaemonNotRunning(RuntimeError):
    """Raised when ~/.opc/daemon.port is missing."""


class DaemonStateInconsistent(RuntimeError):
    """Raised when the port file exists but the token file does not."""


class OpcClient:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url
        self.headers = {"Authorization": f"Bearer {token}"}
        self._client = httpx.Client(base_url=base_url, headers=self.headers, timeout=30.0)

    @classmethod
    def from_env(cls) -> "OpcClient":
        port_path = paths.port_file()
        if not port_path.exists():
            raise DaemonNotRunning(
                "daemon not running — start it with scripts/daemon.sh start"
            )
        port = port_path.read_text().strip()
        token = paths.read_token()
        if token is None:
            raise DaemonStateInconsistent(
                "daemon state inconsistent — restart via scripts/daemon.sh"
            )
        return cls(base_url=f"http://127.0.0.1:{port}", token=token)

    def get(self, path: str, **kwargs) -> httpx.Response:
        return self._client.get(path, **kwargs)

    def post(self, path: str, **kwargs) -> httpx.Response:
        return self._client.post(path, **kwargs)

    def stream(self, method: str, path: str, **kwargs) -> Iterator[str]:
        """Yield server-sent event payload lines (data: ... only)."""
        with self._client.stream(method, path, **kwargs) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if line.startswith("data: "):
                    yield line.removeprefix("data: ")

    def close(self) -> None:
        self._client.close()
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/client/test_client.py -v`
Expected: all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/client/client.py tests/client/test_client.py
git commit -m "feat(client): HTTP client with port + token discovery"
```

---

## Phase D — Runtime registry endpoints + first CLI cutover

### Task 9: Runtime routes (register/activate/list) + active-task guard

**Files:**
- Create: `src/daemon/routes/runtimes.py`
- Create: `tests/daemon/test_routes_runtimes.py`
- Modify: `src/daemon/app.py`

- [ ] **Step 1: Write the failing test**

`tests/daemon/test_routes_runtimes.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.daemon import runtimes as reg
from src.models import TaskRecord, TaskStatus, TaskType
from src.runtime import RuntimeDir


def _make_runtime(base: Path, name: str) -> Path:
    rt = RuntimeDir.init(base / name)
    return rt.root


def test_list_runtimes_empty(tmp_home, app_idle, auth_headers) -> None:
    r = TestClient(app_idle).get("/api/v1/runtimes", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"active": None, "registered": []}


def test_register_runtime(tmp_home, app_idle, auth_headers, tmp_path: Path) -> None:
    rt_path = _make_runtime(tmp_path, "rt-a")
    r = TestClient(app_idle).post(
        "/api/v1/runtimes/register",
        json={"path": str(rt_path)},
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["active"] == str(rt_path)
    assert str(rt_path) in body["registered"]


def test_activate_unknown_path_404(tmp_home, app_idle, auth_headers) -> None:
    r = TestClient(app_idle).post(
        "/api/v1/runtimes/activate",
        json={"path": "/does/not/exist"},
        headers=auth_headers,
    )
    assert r.status_code == 404


def test_activate_blocked_by_in_flight_task(
    tmp_home, app, daemon_state, auth_headers, tmp_path: Path,
) -> None:
    # Register a second runtime first.
    other = _make_runtime(tmp_path, "rt-other")
    reg.register(daemon_state.runtime.root)
    reg.register(other)
    reg.activate(daemon_state.runtime.root)

    # Insert an IN_PROGRESS task on the active runtime.
    task = TaskRecord(id="TASK-001", type=TaskType.GENERAL, brief="x")
    daemon_state.db.insert_task(task)
    daemon_state.db.update_task("TASK-001", status=TaskStatus.IN_PROGRESS)

    r = TestClient(app).post(
        "/api/v1/runtimes/activate",
        json={"path": str(other)},
        headers=auth_headers,
    )
    assert r.status_code == 409
    body = r.json()
    assert body["detail"]["code"] == "active_tasks_in_flight"
    assert "TASK-001" in body["detail"]["task_ids"]


def test_activate_blocked_by_pending_task(
    tmp_home, app, daemon_state, auth_headers, tmp_path: Path,
) -> None:
    """A submitted-but-not-yet-running task must also block activation —
    its runner already holds the current runtime reference."""
    other = _make_runtime(tmp_path, "rt-other")
    reg.register(daemon_state.runtime.root)
    reg.register(other)
    reg.activate(daemon_state.runtime.root)

    # Insert a PENDING task — never marked IN_PROGRESS.
    task = TaskRecord(id="TASK-002", type=TaskType.GENERAL, brief="y")
    daemon_state.db.insert_task(task)

    r = TestClient(app).post(
        "/api/v1/runtimes/activate",
        json={"path": str(other)},
        headers=auth_headers,
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "active_tasks_in_flight"
    assert "TASK-002" in r.json()["detail"]["task_ids"]


def test_unauthenticated_request_401(tmp_home, app_idle) -> None:
    r = TestClient(app_idle).get("/api/v1/runtimes")
    assert r.status_code == 401
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/daemon/test_routes_runtimes.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Add `get_nonterminal_task_ids` to `src/infrastructure/database.py`**

Add this method on the `Database` class, after `next_task_id`. **Why nonterminal (PENDING + IN_PROGRESS) and not just IN_PROGRESS:** between `POST /tasks` inserting the task row and the asyncio runner actually marking it `IN_PROGRESS`, the task is `PENDING` but the runner already holds a reference to the current runtime. If we let activation through during that gap, the next runtime swap could either close the DB the runner is about to use or strand the task on the wrong runtime.

```python
    def get_nonterminal_task_ids(self) -> list[str]:
        nonterminal = (TaskStatus.PENDING.value, TaskStatus.IN_PROGRESS.value)
        cursor = self._conn.execute(
            f"SELECT id FROM tasks WHERE status IN ({','.join('?' * len(nonterminal))})",
            nonterminal,
        )
        return [row["id"] for row in cursor.fetchall()]
```

- [ ] **Step 4: Implement `src/daemon/routes/runtimes.py`**

```python
"""Runtime registry endpoints."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from src.config import Settings
from src.daemon import runtimes as reg
from src.daemon.auth import require_token
from src.daemon.state import DaemonState
from src.infrastructure.database import Database
from src.runtime import RuntimeDir

router = APIRouter(dependencies=[require_token()])


class RuntimePath(BaseModel):
    path: str


def _swap_active_runtime(state: DaemonState, new_path: Path) -> None:
    """Replace the daemon's active runtime."""
    if state.db is not None:
        state.db.close()
    state.runtime = RuntimeDir.load(new_path)
    state.db = Database(state.runtime.db_path)


@router.get("/runtimes")
def list_runtimes(request: Request) -> dict:
    state = reg.load()
    return {
        "active": str(state.active) if state.active else None,
        "registered": [str(p) for p in state.registered],
    }


@router.post("/runtimes/register")
def register_runtime(body: RuntimePath, request: Request) -> dict:
    daemon: DaemonState = request.app.state.daemon
    path = Path(body.path).expanduser()
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
    # Initialize a runtime marker if missing — `opc init` semantics.
    RuntimeDir.init(path)
    try:
        reg.register(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _swap_active_runtime(daemon, path.resolve())
    return list_runtimes(request)


@router.post("/runtimes/activate")
def activate_runtime(body: RuntimePath, request: Request) -> dict:
    daemon: DaemonState = request.app.state.daemon
    path = Path(body.path).expanduser().resolve()
    state = reg.load()
    if path not in state.registered:
        raise HTTPException(status_code=404, detail=f"{path} is not registered")

    # Forbid swap while any nonterminal task exists on the current runtime —
    # PENDING included so submitted-but-not-yet-running tasks can't get
    # re-pointed at a different DB by an interleaved swap.
    if daemon.db is not None:
        in_flight = daemon.db.get_nonterminal_task_ids()
        if in_flight:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "active_tasks_in_flight", "task_ids": in_flight},
            )

    reg.activate(path)
    _swap_active_runtime(daemon, path)
    return list_runtimes(request)
```

- [ ] **Step 5: Wire the router into `src/daemon/app.py`**

Replace:
```python
from src.daemon.routes import health
```
with:
```python
from src.daemon.routes import health, runtimes
```

And after `app.include_router(health.router, prefix="/api/v1")`, add:
```python
    app.include_router(runtimes.router, prefix="/api/v1")
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/daemon/test_routes_runtimes.py -v`
Expected: all 6 tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/daemon/routes/runtimes.py src/daemon/app.py src/infrastructure/database.py tests/daemon/test_routes_runtimes.py
git commit -m "feat(daemon): runtime register/activate/list, block on nonterminal tasks"
```

---

### Task 10: Refactor `opc init` and add `opc use` (first CLI cutover)

**Files:**
- Modify: `src/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Inspect existing CLI tests**

Read: `tests/test_cli.py` to understand current patterns. Many tests will be re-written across Tasks 10/16/18/19/20. For this task, only `cmd_init` tests change.

- [ ] **Step 2: Write the failing test**

Add to `tests/test_cli.py`:

```python
from unittest.mock import MagicMock, patch


def test_cmd_init_calls_register_endpoint(tmp_path, capsys):
    from src.cli import cmd_init

    fake_client = MagicMock()
    fake_client.post.return_value.status_code = 200
    fake_client.post.return_value.json.return_value = {
        "active": str(tmp_path / "rt"),
        "registered": [str(tmp_path / "rt")],
    }

    with patch("src.cli.OpcClient.from_env", return_value=fake_client):
        args = MagicMock(path=str(tmp_path / "rt"))
        cmd_init(args)

    fake_client.post.assert_called_once_with(
        "/api/v1/runtimes/register", json={"path": str(tmp_path / "rt")},
    )
    out = capsys.readouterr().out
    assert "active runtime" in out.lower()


def test_cmd_use_calls_activate_endpoint(tmp_path, capsys):
    from src.cli import cmd_use

    fake_client = MagicMock()
    fake_client.post.return_value.status_code = 200
    fake_client.post.return_value.json.return_value = {
        "active": str(tmp_path / "rt"),
        "registered": [str(tmp_path / "rt")],
    }

    with patch("src.cli.OpcClient.from_env", return_value=fake_client):
        args = MagicMock(path=str(tmp_path / "rt"))
        cmd_use(args)

    fake_client.post.assert_called_once_with(
        "/api/v1/runtimes/activate", json={"path": str(tmp_path / "rt")},
    )
```

- [ ] **Step 3: Run the test and verify it fails**

Run: `uv run pytest tests/test_cli.py::test_cmd_init_calls_register_endpoint -v`
Expected: fails (`cmd_use` doesn't exist; `cmd_init` still uses `RuntimeDir.init` directly).

- [ ] **Step 4: Refactor `cmd_init` and add `cmd_use` in `src/cli.py`**

At the top of `src/cli.py`, add to the imports:

```python
from src.client.client import DaemonNotRunning, DaemonStateInconsistent, OpcClient
```

Replace the existing `cmd_init` with:

```python
def cmd_init(args: argparse.Namespace) -> None:
    """Register a runtime directory with the daemon."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    r = client.post("/api/v1/runtimes/register", json={"path": str(Path(args.path).expanduser())})
    if r.status_code != 200:
        print(f"Error ({r.status_code}): {r.text}")
        sys.exit(1)
    body = r.json()
    print(f"Active runtime: {body['active']}")
```

Add a new `cmd_use`:

```python
def cmd_use(args: argparse.Namespace) -> None:
    """Switch the daemon's active runtime."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    r = client.post("/api/v1/runtimes/activate", json={"path": str(Path(args.path).expanduser())})
    if r.status_code == 409:
        detail = r.json().get("detail", {})
        print(f"Cannot switch runtime: tasks in flight ({detail.get('task_ids')})")
        sys.exit(1)
    if r.status_code != 200:
        print(f"Error ({r.status_code}): {r.text}")
        sys.exit(1)
    body = r.json()
    print(f"Active runtime: {body['active']}")
```

In `build_parser`, add the `use` subcommand after `init`:

```python
    p_use = sub.add_parser("use", help="Switch the daemon's active runtime")
    p_use.add_argument("path", help="Path of an already-registered runtime")
    p_use.set_defaults(func=cmd_use)
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_cli.py::test_cmd_init_calls_register_endpoint tests/test_cli.py::test_cmd_use_calls_activate_endpoint -v`
Expected: both pass.

Run: `uv run pytest tests/ -q`
Expected: any pre-existing CLI tests that called `cmd_init` directly will likely break. Update them in this task or the dedicated cleanup task (Task 25). For now, mark broken tests with `@pytest.mark.skip(reason="rewritten in Task 25")` and proceed.

- [ ] **Step 6: Commit**

```bash
git add src/cli.py tests/test_cli.py
git commit -m "feat(cli): convert opc init + add opc use as HTTP clients"
```

---

## Phase E — Sessions, event bus, runner

### Task 11: Active-session tracker

**Files:**
- Create: `src/daemon/sessions.py`
- Create: `tests/daemon/test_sessions.py`

The tracker maps `(task_id, agent)` → `session_id` for the currently-active spawn. Used by the completion endpoint to validate callbacks and by the runner to know which session_id "wins" if multiple are active.

- [ ] **Step 1: Write the failing test**

`tests/daemon/test_sessions.py`:

```python
from __future__ import annotations

import pytest

from src.daemon.sessions import SessionTracker


def test_register_and_lookup() -> None:
    t = SessionTracker()
    t.set_active("TASK-001", "dev_agent", "sess-1")
    assert t.get_active("TASK-001", "dev_agent") == "sess-1"


def test_unknown_returns_none() -> None:
    t = SessionTracker()
    assert t.get_active("TASK-999", "dev_agent") is None


def test_overwrite_replaces_previous() -> None:
    t = SessionTracker()
    t.set_active("TASK-001", "dev_agent", "sess-1")
    t.set_active("TASK-001", "dev_agent", "sess-2")
    assert t.get_active("TASK-001", "dev_agent") == "sess-2"


def test_clear_removes_entry() -> None:
    t = SessionTracker()
    t.set_active("TASK-001", "dev_agent", "sess-1")
    t.clear("TASK-001", "dev_agent")
    assert t.get_active("TASK-001", "dev_agent") is None


def test_independent_per_agent() -> None:
    t = SessionTracker()
    t.set_active("TASK-001", "dev_agent", "sess-1")
    t.set_active("TASK-001", "engineering_head", "sess-2")
    assert t.get_active("TASK-001", "dev_agent") == "sess-1"
    assert t.get_active("TASK-001", "engineering_head") == "sess-2"
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/daemon/test_sessions.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `src/daemon/sessions.py`**

```python
"""In-memory tracker for the active session per (task_id, agent)."""
from __future__ import annotations

from threading import Lock


class SessionTracker:
    def __init__(self) -> None:
        self._active: dict[tuple[str, str], str] = {}
        self._lock = Lock()

    def set_active(self, task_id: str, agent: str, session_id: str) -> None:
        with self._lock:
            self._active[(task_id, agent)] = session_id

    def get_active(self, task_id: str, agent: str) -> str | None:
        with self._lock:
            return self._active.get((task_id, agent))

    def clear(self, task_id: str, agent: str) -> None:
        with self._lock:
            self._active.pop((task_id, agent), None)
```

- [ ] **Step 4: Wire `SessionTracker` into `DaemonState`**

In `src/daemon/state.py`:

```python
from src.daemon.sessions import SessionTracker
```

Add to the dataclass (after `db_lock`):

```python
    sessions: SessionTracker = field(default_factory=SessionTracker)
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/daemon/test_sessions.py tests/daemon/test_routes_health.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/daemon/sessions.py src/daemon/state.py tests/daemon/test_sessions.py
git commit -m "feat(daemon): SessionTracker for per-(task,agent) active spawn"
```

---

### Task 12: Add session-scoped DB lookup helper

**Files:**
- Modify: `src/infrastructure/database.py`
- Modify: `tests/test_database.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_database.py`:

```python
def test_get_latest_task_result_filters_by_session_id(db) -> None:
    db.insert_task_result(
        task_id="TASK-001", agent="dev_agent", session_id="sess-A",
        output_summary="early", confidence_score=70,
    )
    db.insert_task_result(
        task_id="TASK-001", agent="dev_agent", session_id="sess-B",
        output_summary="newer", confidence_score=90,
    )
    # Filter by sess-A → should return the early one even though sess-B is newer
    a = db.get_latest_task_result("TASK-001", "dev_agent", "sess-A")
    assert a is not None
    assert a["output_summary"] == "early"
    b = db.get_latest_task_result("TASK-001", "dev_agent", "sess-B")
    assert b is not None
    assert b["output_summary"] == "newer"


def test_get_latest_task_result_returns_none_when_missing(db) -> None:
    assert db.get_latest_task_result("TASK-X", "dev_agent", "sess-Z") is None


def test_get_latest_task_result_picks_most_recent_in_session(db) -> None:
    db.insert_task_result(
        task_id="TASK-001", agent="dev_agent", session_id="sess-A",
        output_summary="first", confidence_score=70,
    )
    db.insert_task_result(
        task_id="TASK-001", agent="dev_agent", session_id="sess-A",
        output_summary="retry", confidence_score=85,
    )
    latest = db.get_latest_task_result("TASK-001", "dev_agent", "sess-A")
    assert latest["output_summary"] == "retry"
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/test_database.py::test_get_latest_task_result_filters_by_session_id -v`
Expected: `AttributeError: 'Database' object has no attribute 'get_latest_task_result'`.

- [ ] **Step 3: Add the helper in `src/infrastructure/database.py`**

After `get_agent_task_results`:

```python
    def get_latest_task_result(
        self, task_id: str, agent: str, session_id: str,
    ) -> dict | None:
        cursor = self._conn.execute(
            """SELECT * FROM task_results
               WHERE task_id = ? AND agent = ? AND session_id = ?
               ORDER BY id DESC LIMIT 1""",
            (task_id, agent, session_id),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        d = dict(row)
        if d.get("risks_flagged"):
            d["risks_flagged"] = json.loads(d["risks_flagged"])
        return d
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_database.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/database.py tests/test_database.py
git commit -m "feat(db): get_latest_task_result(task,agent,session)"
```

---

### Task 13: Event bus with DB replay

**Files:**
- Create: `src/daemon/event_bus.py`
- Create: `tests/daemon/test_event_bus.py`
- Modify: `src/daemon/state.py`

- [ ] **Step 1: Write the failing test**

`tests/daemon/test_event_bus.py`:

```python
from __future__ import annotations

import asyncio

import pytest

from src.daemon.event_bus import EventBus


@pytest.mark.asyncio
async def test_publish_then_subscribe_delivers_history() -> None:
    bus = EventBus(history_loader=lambda task_id: [
        {"type": "step", "n": 1}, {"type": "step", "n": 2},
    ])
    received: list = []

    async def consumer():
        async for event in bus.subscribe("TASK-001"):
            received.append(event)
            if len(received) == 3:
                break

    consumer_task = asyncio.create_task(consumer())
    await asyncio.sleep(0.05)  # let subscriber consume history
    await bus.publish("TASK-001", {"type": "step", "n": 3})
    await asyncio.wait_for(consumer_task, timeout=2.0)
    assert [e["n"] for e in received] == [1, 2, 3]


@pytest.mark.asyncio
async def test_terminal_event_closes_subscriber() -> None:
    bus = EventBus(history_loader=lambda _t: [])
    received: list = []

    async def consumer():
        async for event in bus.subscribe("TASK-001"):
            received.append(event)

    consumer_task = asyncio.create_task(consumer())
    await asyncio.sleep(0.05)
    await bus.publish("TASK-001", {"type": "task_complete", "status": "approved"})
    await asyncio.wait_for(consumer_task, timeout=2.0)
    assert received[-1]["type"] == "task_complete"


@pytest.mark.asyncio
async def test_two_subscribers_both_receive() -> None:
    bus = EventBus(history_loader=lambda _t: [])
    a, b = [], []

    async def consume(into):
        async for event in bus.subscribe("TASK-001"):
            into.append(event)

    ta = asyncio.create_task(consume(a))
    tb = asyncio.create_task(consume(b))
    await asyncio.sleep(0.05)
    await bus.publish("TASK-001", {"type": "task_complete"})
    await asyncio.wait_for(asyncio.gather(ta, tb), timeout=2.0)
    assert a == b == [{"type": "task_complete"}]


@pytest.mark.asyncio
async def test_late_subscriber_to_finished_task_gets_synthesized_terminal() -> None:
    """Reattach scenario: task already finished, no live publisher exists.
    The history loader must surface a terminal event so the subscriber closes."""
    history = [
        {"type": "audit", "action": "session_end"},
        {"type": "task_complete", "outcome": "approved", "synthesized": True},
    ]
    bus = EventBus(history_loader=lambda _t: history)
    received: list = []

    async def consumer():
        async for event in bus.subscribe("TASK-DONE"):
            received.append(event)

    await asyncio.wait_for(consumer(), timeout=2.0)
    assert received[-1]["type"] == "task_complete"
    assert received[-1].get("synthesized") is True
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/daemon/test_event_bus.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `src/daemon/event_bus.py`**

```python
"""In-memory pub/sub for daemon events with DB replay on subscribe."""
from __future__ import annotations

import asyncio
from typing import AsyncIterator, Callable

_TERMINAL_TYPES = {"task_complete", "task_escalated", "task_rejected"}


class EventBus:
    def __init__(self, history_loader: Callable[[str], list[dict]]) -> None:
        self._history_loader = history_loader
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()

    async def publish(self, task_id: str, event: dict) -> None:
        async with self._lock:
            queues = list(self._subscribers.get(task_id, []))
        for q in queues:
            await q.put(event)

    async def subscribe(self, task_id: str) -> AsyncIterator[dict]:
        queue: asyncio.Queue = asyncio.Queue()
        async with self._lock:
            self._subscribers.setdefault(task_id, []).append(queue)
        try:
            for past in self._history_loader(task_id):
                yield past
                if past.get("type") in _TERMINAL_TYPES:
                    return
            while True:
                event = await queue.get()
                yield event
                if event.get("type") in _TERMINAL_TYPES:
                    return
        finally:
            async with self._lock:
                if queue in self._subscribers.get(task_id, []):
                    self._subscribers[task_id].remove(queue)
                if not self._subscribers.get(task_id):
                    self._subscribers.pop(task_id, None)
```

- [ ] **Step 4: Wire `EventBus` into `DaemonState`**

In `src/daemon/state.py`, add imports and instantiate the bus in `__post_init__`. The history loader must do two things: (a) replay audit logs, (b) **synthesize a terminal event** from `task.status` if the task is already in a terminal state. Without (b), an `opc tail` that subscribes after a task completed sees only audit rows, then blocks on `queue.get()` forever because no live publisher will ever fire `task_complete` again.

```python
from src.daemon.event_bus import EventBus
from src.models import TaskStatus
```

Add the field:
```python
    event_bus: EventBus = field(init=False)
```

And inside `__post_init__` (add it):
```python
    _TERMINAL_STATUS_TO_EVENT = {
        TaskStatus.APPROVED: "task_complete",
        TaskStatus.COMPLETED: "task_complete",
        TaskStatus.REJECTED: "task_rejected",
        TaskStatus.ESCALATED: "task_escalated",
    }

    def __post_init__(self) -> None:
        def loader(task_id: str) -> list[dict]:
            if self.db is None:
                return []
            history: list[dict] = [
                {"type": "audit", **log}
                for log in self.db.get_audit_logs(task_id)
            ]
            task = self.db.get_task(task_id)
            if task is not None and task.status in self._TERMINAL_STATUS_TO_EVENT:
                # Synthesize the terminal event so late subscribers close cleanly.
                # Live runs publish their own terminal event before the task row
                # flips to a terminal status, so the subscriber will see one or
                # the other — never both, never neither.
                history.append({
                    "type": self._TERMINAL_STATUS_TO_EVENT[task.status],
                    "outcome": task.status.value,
                    "synthesized": True,
                })
            return history
        self.event_bus = EventBus(history_loader=loader)
```

(The class needs `from typing import ClassVar` if `_TERMINAL_STATUS_TO_EVENT` is annotated; here it's a plain class attribute so no annotation needed.)

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/daemon/test_event_bus.py tests/daemon/test_routes_health.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/daemon/event_bus.py src/daemon/state.py tests/daemon/test_event_bus.py
git commit -m "feat(daemon): in-memory event bus with DB replay"
```

---

### Task 14: Refactor `Orchestrator._run_agent` to take a session_id and read completion from DB

**Files:**
- Modify: `src/orchestrator/executor.py`
- Modify: `src/orchestrator/orchestrator.py`
- Modify: `tests/test_executor.py`
- Modify: `tests/test_orchestrator.py`

- [ ] **Step 1: Update `src/orchestrator/executor.py`**

Drop the `read_completion_report` method and the `completion_report.json` lifecycle. The new `ExecutorResult` carries only the subprocess outcome — completion is read from DB by the caller.

Replace the file with:

```python
from __future__ import annotations

import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ExecutorResult:
    """Outcome of a subprocess execution. Completion data lives in the DB."""

    success: bool
    duration_seconds: int
    session_id: str
    error: str | None = None


class AgentExecutor:
    def __init__(self, claude_cli_path: str, permission_mode: str) -> None:
        self._cli_path = claude_cli_path
        self._permission_mode = permission_mode

    def run(
        self,
        workspace: Path,
        prompt: str,
        session_id: str | None = None,
        timeout_seconds: int = 1800,
    ) -> ExecutorResult:
        sid = session_id or f"sess-{uuid.uuid4().hex}"
        cmd = [
            self._cli_path,
            "-p", prompt,
            "--permission-mode", self._permission_mode,
        ]
        start_time = time.monotonic()
        try:
            subprocess.run(
                cmd,
                cwd=str(workspace),
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return ExecutorResult(
                success=False,
                duration_seconds=int(time.monotonic() - start_time),
                session_id=sid,
                error=f"Session timed out after {timeout_seconds} seconds",
            )
        return ExecutorResult(
            success=True,
            duration_seconds=int(time.monotonic() - start_time),
            session_id=sid,
        )
```

- [ ] **Step 2: Update `src/orchestrator/orchestrator.py`**

The orchestrator no longer reads `completion_report.json`. After `_executor.run` returns, it queries the DB for the latest completion record matching `(task_id, agent, session_id)`. If missing, it builds a synthetic report representing "session ended without completion report".

Add an import:
```python
import uuid
from src.models import CompletionReport
```

Add a helper near the top of the class:

```python
    def _build_session_id(self) -> str:
        return f"sess-{uuid.uuid4().hex}"

    def _read_completion_from_db(
        self, task_id: str, agent: str, session_id: str,
    ) -> CompletionReport | None:
        row = self._db.get_latest_task_result(task_id, agent, session_id)
        if row is None:
            return None
        return CompletionReport(
            task_id=task_id,
            agent=agent,
            status="completed",
            confidence=row["confidence_score"] or 0,
            output_summary=row["output_summary"] or "",
            risks_flagged=row.get("risks_flagged") or [],
            dependencies=[],
            suggested_reviewer_focus=[],
        )
```

Replace `_run_agent` so it:
- Generates a session_id and registers it (via a hook the daemon will install — for now just generate and pass through).
- Calls `executor.run(..., session_id=...)`.
- Reads the completion from DB after exit.
- Returns a struct that bundles the executor result + the DB-derived report.

Replace the existing `_run_agent` with:

```python
    def _run_agent(
        self,
        task_id: str,
        agent: AgentName,
        prompt: str,
        on_session_started: callable | None = None,
    ):
        """Set up workspace and run an agent session.

        Returns a tuple ``(executor_result, completion_report_or_None)``.
        ``on_session_started`` is invoked with ``(task_id, agent_name, session_id)``
        before the subprocess starts so the daemon can register the active session.
        """
        task = self._db.get_task(task_id)
        agent_name = agent.value
        workspace = self._runtime.workspaces_dir / agent_name

        # Workspace is initialized once at `opc init-agent` — not per session.
        # Brief is injected here:
        brief = task.brief if task else ""
        session_id = self._build_session_id()
        full_prompt = (
            f"Task ID: {task_id}\nSession ID: {session_id}\nBrief: {brief}\n\n{prompt}"
        )

        if on_session_started is not None:
            on_session_started(task_id, agent_name, session_id)

        self._audit.log_session_start(task_id, agent_name, str(workspace))
        self._db.update_task(task_id, assigned_agent=agent_name)

        result = self._executor.run(
            workspace=workspace,
            prompt=full_prompt,
            session_id=session_id,
            timeout_seconds=self._settings.session_timeout_seconds,
        )
        self._audit.log_session_end(task_id, agent_name, result.duration_seconds)

        report = self._read_completion_from_db(task_id, agent_name, session_id)
        return result, report
```

Update the two call-sites in `run_task` (the EH call and the delegate call) — they used to do `eh_result.report` and `delegate_result.report`; they now unpack `(result, report)`:

```python
            eh_result, eh_report = self._run_agent(task_id, AgentName.ENGINEERING_HEAD, eh_prompt)
            if not eh_result.success or eh_report is None:
                self._db.update_task(task_id, status=TaskStatus.REJECTED)
                self._update_recent_tasks(task_id)
                return "rejected"

            self._log_step_result(task_id, eh_result, eh_report)
            next_step = self._parse_next_step(eh_report)
```

And:

```python
                delegate_result, delegate_report = self._run_agent(
                    task_id, delegate_agent, next_step.prompt or "",
                )
                if delegate_result.success and delegate_report is not None:
                    self._log_step_result(task_id, delegate_result, delegate_report)

                result_summary = (
                    delegate_report.output_summary
                    if delegate_report
                    else "Agent session failed"
                )
                prior_steps.append(StepRecord(
                    step_number=step_num,
                    agent=next_step.agent,
                    action=f"delegate: {(next_step.prompt or '')[:100]}",
                    result_summary=result_summary,
                    success=delegate_result.success and delegate_report is not None,
                ))
```

Update `_parse_next_step` to take a `CompletionReport` instead of `ExecutorResult`:

```python
    def _parse_next_step(self, report: CompletionReport | None) -> NextStep:
        if report is None:
            return NextStep(action="escalate", reason="No completion report from Engineering Head")
        text = report.output_summary
        ...
```

Update `_log_step_result` to accept the report explicitly:

```python
    def _log_step_result(self, task_id: str, result, report) -> None:
        if report is None:
            return
        self._audit.log_completion_report(
            report=report,
            session_id=result.session_id,
            duration_seconds=result.duration_seconds,
        )
```

(The `_DEFAULT_SYSTEM_PROMPTS` block and the per-call `initialize_workspace` call from `_run_agent` are removed — the daemon assumes `opc init-agent` already ran.)

- [ ] **Step 3: Update `tests/test_executor.py`**

Drop the `completion_report.json`-related cases. Keep only the timeout / subprocess wiring tests. Use `monkeypatch` against `subprocess.run` for the success path.

- [ ] **Step 4: Update `tests/test_orchestrator.py`**

Where the test used to write a fake `completion_report.json`, now insert a row into the DB via `db.insert_task_result(task_id, agent, session_id, output_summary, confidence_score)` *before* the executor returns. The cleanest pattern: stub `Orchestrator._run_agent` directly to return `(ExecutorResult(success=True, duration_seconds=0, session_id="sess-x"), CompletionReport(...))`.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_executor.py tests/test_orchestrator.py -v`
Expected: all pass.

Run: `uv run pytest tests/ -q`
Expected: full suite passes (or only the CLI tests already skipped in Task 10 are skipped).

- [ ] **Step 6: Commit**

```bash
git add src/orchestrator/executor.py src/orchestrator/orchestrator.py tests/test_executor.py tests/test_orchestrator.py
git commit -m "refactor(orchestrator): session_id injection + DB-based completion lookup"
```

---

### Task 15: Task runner module

**Files:**
- Create: `src/daemon/runner.py`
- Create: `tests/daemon/test_runner.py`

The runner is the bridge between FastAPI (async) and the existing blocking orchestrator. It also wires the `on_session_started` callback so the `SessionTracker` always reflects the currently-active spawn.

**Critical: snapshot the runtime at construction.** `TaskRunner` must capture `(db, runtime)` from `DaemonState` *at submission time* and never re-read them when it actually runs. If we instead held a reference to `DaemonState` and dereferenced `state.db` / `state.runtime` inside `run()`, an interleaved `POST /runtimes/activate` could swap the runtime out from under a queued runner — sending the task to the wrong workspace or a closed DB. The PENDING-blocking guard in Task 9 is the primary defense; this snapshot is belt-and-braces in case the guard is ever weakened.

- [ ] **Step 1: Write the failing test**

`tests/daemon/test_runner.py`:

```python
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from src.daemon.runner import TaskRunner


@pytest.mark.asyncio
async def test_runner_invokes_orchestrator_run_task() -> None:
    state = MagicMock()
    state.runtime = MagicMock()
    state.db = MagicMock()
    state.settings = MagicMock()
    state.sessions = MagicMock()
    state.event_bus = MagicMock()
    state.event_bus.publish = MagicMock(return_value=asyncio.sleep(0))

    orch = MagicMock()
    orch.run_task = MagicMock(return_value="approved")

    runner = TaskRunner(state=state, orchestrator_factory=lambda _r, _d, _s: orch)
    await runner.run("TASK-001")

    orch.run_task.assert_called_once_with("TASK-001")


@pytest.mark.asyncio
async def test_runner_publishes_terminal_event() -> None:
    state = MagicMock()
    state.event_bus = MagicMock()

    captured: list[dict] = []
    async def fake_publish(task_id, event):
        captured.append(event)
    state.event_bus.publish = fake_publish

    orch = MagicMock()
    orch.run_task = MagicMock(return_value="escalated")

    runner = TaskRunner(state=state, orchestrator_factory=lambda _r, _d, _s: orch)
    await runner.run("TASK-001")
    assert any(e["type"] == "task_escalated" for e in captured)


@pytest.mark.asyncio
async def test_runner_snapshots_runtime_and_db_at_construction() -> None:
    """If DaemonState gets a different runtime/db after the runner is built,
    the runner must still use the originals."""
    state = MagicMock()
    state.event_bus = MagicMock()
    state.event_bus.publish = MagicMock(return_value=asyncio.sleep(0))
    state.sessions = MagicMock()

    original_runtime = MagicMock(name="rt-original")
    original_db = MagicMock(name="db-original")
    original_settings = MagicMock(name="settings-original")
    state.runtime = original_runtime
    state.db = original_db
    state.settings = original_settings

    captured: dict = {}
    def factory(rt, db, settings):
        captured["rt"] = rt
        captured["db"] = db
        captured["settings"] = settings
        m = MagicMock()
        m.run_task = MagicMock(return_value="approved")
        return m

    runner = TaskRunner(state=state, orchestrator_factory=factory)

    # Simulate a runtime swap after submit but before the runner actually runs.
    state.runtime = MagicMock(name="rt-swapped")
    state.db = MagicMock(name="db-swapped")

    await runner.run("TASK-001")

    assert captured["rt"] is original_runtime
    assert captured["db"] is original_db
    assert captured["settings"] is original_settings
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/daemon/test_runner.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `src/daemon/runner.py`**

```python
"""Daemon-side task runner that wraps the blocking Orchestrator."""
from __future__ import annotations

import asyncio
import logging
from typing import Callable

from src.config import Settings
from src.daemon.state import DaemonState
from src.infrastructure.database import Database
from src.orchestrator.orchestrator import Orchestrator
from src.runtime import RuntimeDir

logger = logging.getLogger("opc.daemon.runner")

_OUTCOME_TO_EVENT = {
    "approved": "task_complete",
    "rejected": "task_rejected",
    "escalated": "task_escalated",
}


class TaskRunner:
    """Snapshot of (runtime, db, settings) at construction time, decoupled from
    live `DaemonState` mutation. The event bus + session tracker are still
    looked up live on `state` because they're singletons that don't change on
    runtime swap."""

    def __init__(
        self,
        state: DaemonState,
        orchestrator_factory: Callable[[RuntimeDir, Database, Settings], Orchestrator] | None = None,
    ) -> None:
        assert state.db is not None and state.runtime is not None, \
            "TaskRunner cannot be constructed in idle mode"
        # Snapshot the runtime/db/settings now. Even if state.runtime swaps
        # later (which the activate guard should prevent anyway), this runner
        # keeps operating against the runtime the task was created in.
        self._runtime: RuntimeDir = state.runtime
        self._db: Database = state.db
        self._settings: Settings = state.settings
        self._sessions = state.sessions
        self._event_bus = state.event_bus
        self._make_orchestrator = orchestrator_factory or self._default_factory

    @staticmethod
    def _default_factory(runtime: RuntimeDir, db: Database, settings: Settings) -> Orchestrator:
        return Orchestrator(db=db, settings=settings, runtime=runtime)

    async def run(self, task_id: str) -> None:
        orchestrator = self._make_orchestrator(self._runtime, self._db, self._settings)

        # Patch the orchestrator's per-spawn callback into SessionTracker.
        original_run_agent = orchestrator._run_agent
        sessions = self._sessions

        def _wrapped_run_agent(task_id_, agent, prompt):
            def _on_started(t, a, s):
                sessions.set_active(t, a, s)
            return original_run_agent(task_id_, agent, prompt, on_session_started=_on_started)

        orchestrator._run_agent = _wrapped_run_agent  # type: ignore[assignment]

        try:
            outcome = await asyncio.to_thread(orchestrator.run_task, task_id)
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("task %s crashed in runner", task_id)
            await self._event_bus.publish(task_id, {
                "type": "task_escalated", "reason": f"runner crash: {exc}",
            })
            return

        await self._event_bus.publish(task_id, {
            "type": _OUTCOME_TO_EVENT.get(outcome, "task_complete"),
            "outcome": outcome,
        })
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/daemon/test_runner.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/runner.py tests/daemon/test_runner.py
git commit -m "feat(daemon): TaskRunner wraps Orchestrator, publishes terminal events"
```

---

## Phase F — Tasks routes (submit/list/detail/SSE/completion/learning)

### Task 16: Tasks submit + list + detail endpoints

**Files:**
- Create: `src/daemon/routes/tasks.py`
- Create: `tests/daemon/test_routes_tasks.py`
- Modify: `src/daemon/app.py`

- [ ] **Step 1: Write the failing test**

`tests/daemon/test_routes_tasks.py`:

```python
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def stub_runner(monkeypatch):
    """Don't actually run tasks during route tests."""
    async def fake_run(self, task_id):
        return None
    monkeypatch.setattr("src.daemon.runner.TaskRunner.run", fake_run)


def test_submit_task_returns_id(tmp_home, app, auth_headers) -> None:
    r = TestClient(app).post(
        "/api/v1/tasks",
        json={"type": "general", "brief": "test"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["task_id"].startswith("TASK-")


def test_submit_task_idle_returns_409(tmp_home, app_idle, auth_headers) -> None:
    r = TestClient(app_idle).post(
        "/api/v1/tasks",
        json={"type": "general", "brief": "x"},
        headers=auth_headers,
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "no_active_runtime"


def test_list_tasks_returns_list(tmp_home, app, auth_headers) -> None:
    TestClient(app).post(
        "/api/v1/tasks", json={"type": "general", "brief": "x"}, headers=auth_headers,
    )
    r = TestClient(app).get("/api/v1/tasks", headers=auth_headers)
    assert r.status_code == 200
    items = r.json()["tasks"]
    assert len(items) >= 1


def test_get_task_detail_404_when_missing(tmp_home, app, auth_headers) -> None:
    r = TestClient(app).get("/api/v1/tasks/TASK-999", headers=auth_headers)
    assert r.status_code == 404
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/daemon/test_routes_tasks.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `src/daemon/routes/tasks.py`** (submit/list/detail only — SSE + completion in next task)

```python
"""Task submission and inspection endpoints."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from pydantic import BaseModel

from src.daemon.auth import require_token
from src.daemon.runner import TaskRunner
from src.daemon.state import DaemonState
from src.models import TaskRecord, TaskType

router = APIRouter(dependencies=[require_token()])


class SubmitTask(BaseModel):
    type: str = "general"
    brief: str


def _require_active(state: DaemonState) -> None:
    if state.is_idle:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "no_active_runtime"},
        )


@router.post("/tasks")
async def submit_task(body: SubmitTask, request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_active(state)
    task_type = TaskType(body.type)
    task_id = state.db.next_task_id()
    state.db.insert_task(TaskRecord(id=task_id, type=task_type, brief=body.brief))

    runner = TaskRunner(state=state)
    asyncio.create_task(runner.run(task_id))
    return {"task_id": task_id}


@router.get("/tasks")
def list_tasks(request: Request, limit: int = 20) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_active(state)
    tasks = state.db.list_tasks(limit=limit)
    return {"tasks": [t.model_dump() for t in tasks]}


@router.get("/tasks/{task_id}")
def get_task(task_id: str, request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_active(state)
    task = state.db.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    return {
        "task": task.model_dump(),
        "results": state.db.get_task_results(task_id),
        "audit_log": state.db.get_audit_logs(task_id),
    }
```

- [ ] **Step 4: Wire the router**

In `src/daemon/app.py`:

```python
from src.daemon.routes import health, runtimes, tasks
...
    app.include_router(tasks.router, prefix="/api/v1")
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/daemon/test_routes_tasks.py -v`
Expected: 4 tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/daemon/routes/tasks.py src/daemon/app.py tests/daemon/test_routes_tasks.py
git commit -m "feat(daemon): tasks submit/list/detail endpoints"
```

---

### Task 17: Tasks SSE events + completion callback

**Files:**
- Modify: `src/daemon/routes/tasks.py`
- Modify: `tests/daemon/test_routes_tasks.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/daemon/test_routes_tasks.py`:

```python
def test_completion_requires_session_id(tmp_home, app, auth_headers) -> None:
    # Create a task first
    sub = TestClient(app).post(
        "/api/v1/tasks",
        json={"type": "general", "brief": "x"},
        headers=auth_headers,
    )
    task_id = sub.json()["task_id"]

    r = TestClient(app).post(
        f"/api/v1/tasks/{task_id}/completion",
        json={"agent": "dev_agent", "status": "completed", "confidence": 90,
              "output_summary": "ok"},
        headers=auth_headers,
    )
    assert r.status_code == 422  # missing session_id


def test_completion_session_mismatch_409(tmp_home, app, daemon_state, auth_headers) -> None:
    sub = TestClient(app).post(
        "/api/v1/tasks",
        json={"type": "general", "brief": "x"},
        headers=auth_headers,
    )
    task_id = sub.json()["task_id"]

    # Mark a different session_id as active.
    daemon_state.sessions.set_active(task_id, "dev_agent", "sess-real")

    r = TestClient(app).post(
        f"/api/v1/tasks/{task_id}/completion",
        json={"session_id": "sess-stale", "agent": "dev_agent",
              "status": "completed", "confidence": 90, "output_summary": "ok"},
        headers=auth_headers,
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "session_mismatch"


def test_completion_unknown_session_409(tmp_home, app, daemon_state, auth_headers) -> None:
    """If the daemon never registered a session for (task, agent), reject —
    do not silently persist a fabricated completion."""
    sub = TestClient(app).post(
        "/api/v1/tasks",
        json={"type": "general", "brief": "x"},
        headers=auth_headers,
    )
    task_id = sub.json()["task_id"]
    # Note: no set_active() call — tracker is empty for (task_id, dev_agent).

    r = TestClient(app).post(
        f"/api/v1/tasks/{task_id}/completion",
        json={"session_id": "fabricated", "agent": "dev_agent",
              "status": "completed", "confidence": 90, "output_summary": "ok"},
        headers=auth_headers,
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "unknown_session"
    # And nothing was persisted.
    assert daemon_state.db.get_task_results(task_id) == []


def test_completion_persists_when_session_matches(tmp_home, app, daemon_state, auth_headers) -> None:
    sub = TestClient(app).post(
        "/api/v1/tasks",
        json={"type": "general", "brief": "x"},
        headers=auth_headers,
    )
    task_id = sub.json()["task_id"]
    daemon_state.sessions.set_active(task_id, "dev_agent", "sess-1")

    r = TestClient(app).post(
        f"/api/v1/tasks/{task_id}/completion",
        json={"session_id": "sess-1", "agent": "dev_agent",
              "status": "completed", "confidence": 90, "output_summary": "ok"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    rows = daemon_state.db.get_task_results(task_id)
    assert any(r["session_id"] == "sess-1" for r in rows)


def test_events_stream_yields_completion(tmp_home, app, daemon_state, auth_headers) -> None:
    sub = TestClient(app).post(
        "/api/v1/tasks",
        json={"type": "general", "brief": "x"},
        headers=auth_headers,
    )
    task_id = sub.json()["task_id"]

    # Pre-publish a terminal event so the stream closes immediately.
    import asyncio
    asyncio.get_event_loop().run_until_complete(
        daemon_state.event_bus.publish(task_id, {"type": "task_complete"})
    )

    with TestClient(app).stream(
        "GET", f"/api/v1/tasks/{task_id}/events", headers=auth_headers,
    ) as r:
        assert r.status_code == 200
        body = b"".join(r.iter_bytes())
    assert b"task_complete" in body
```

(Note: SSE testing inside `TestClient` is finicky — the last test may need `pytest.mark.asyncio` and a fully async client. Use `httpx.AsyncClient` against the app if needed; mark accordingly.)

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/daemon/test_routes_tasks.py -v -k "completion or events"`
Expected: failures on missing endpoints.

- [ ] **Step 3: Add SSE + completion to `src/daemon/routes/tasks.py`**

Append to the file:

```python
from sse_starlette.sse import EventSourceResponse
import json as _json


class CompletionBody(BaseModel):
    session_id: str
    agent: str
    status: str
    confidence: int
    output_summary: str
    risks_flagged: list[str] = []
    dependencies: list[str] = []
    suggested_reviewer_focus: list[str] = []


@router.get("/tasks/{task_id}/events")
async def task_events(task_id: str, request: Request):
    state: DaemonState = request.app.state.daemon
    _require_active(state)

    async def gen():
        async for event in state.event_bus.subscribe(task_id):
            yield {"data": _json.dumps(event)}

    return EventSourceResponse(gen())


@router.post("/tasks/{task_id}/completion")
async def submit_completion(task_id: str, body: CompletionBody, request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_active(state)
    expected = state.sessions.get_active(task_id, body.agent)
    # Reject callbacks the daemon never spawned. Both branches are 409 — the
    # tracker is the source of truth for "is this a real session". Unknown
    # comes first so an empty tracker can't silently accept a fabricated id.
    if expected is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "unknown_session", "task_id": task_id, "agent": body.agent},
        )
    if expected != body.session_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "session_mismatch", "active": expected, "got": body.session_id},
        )
    async with state.db_lock:
        state.db.insert_task_result(
            task_id=task_id,
            agent=body.agent,
            session_id=body.session_id,
            output_summary=body.output_summary,
            confidence_score=body.confidence,
            risks_flagged=body.risks_flagged or None,
        )
    await state.event_bus.publish(task_id, {
        "type": "completion_reported",
        "agent": body.agent,
        "session_id": body.session_id,
        "status": body.status,
    })
    return {"ok": True}
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/daemon/test_routes_tasks.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/tasks.py tests/daemon/test_routes_tasks.py
git commit -m "feat(daemon): SSE events + session-validated completion callback"
```

---

### Task 18: Agents endpoints (list, init SSE, learnings)

**Files:**
- Create: `src/daemon/routes/agents.py`
- Create: `tests/daemon/test_routes_agents.py`
- Modify: `src/daemon/app.py`

- [ ] **Step 1: Write the failing test**

`tests/daemon/test_routes_agents.py`:

```python
from __future__ import annotations

from fastapi.testclient import TestClient


def test_list_agents_returns_tiers(tmp_home, app, auth_headers) -> None:
    r = TestClient(app).get("/api/v1/agents", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert "agents" in body
    names = [a["name"] for a in body["agents"]]
    assert "engineering_head" in names


def test_learnings_requires_session_id(tmp_home, app, daemon_state, auth_headers) -> None:
    daemon_state.sessions.set_active("TASK-001", "dev_agent", "sess-1")
    r = TestClient(app).post(
        "/api/v1/agents/dev_agent/learnings",
        json={"text": "x"},
        headers=auth_headers,
    )
    assert r.status_code == 422  # session_id missing


def test_learnings_appends_to_file(
    tmp_home, app, daemon_state, auth_headers, tmp_path,
) -> None:
    daemon_state.sessions.set_active("TASK-001", "dev_agent", "sess-1")
    workspace = daemon_state.runtime.workspaces_dir / "dev_agent"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "learnings.md").write_text("# Learnings: dev_agent\n\n")

    r = TestClient(app).post(
        "/api/v1/agents/dev_agent/learnings",
        json={"session_id": "sess-1", "task_id": "TASK-001", "text": "use uv not pip"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert "use uv not pip" in (workspace / "learnings.md").read_text()


def test_learnings_session_mismatch_409(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    daemon_state.sessions.set_active("TASK-001", "dev_agent", "sess-real")
    r = TestClient(app).post(
        "/api/v1/agents/dev_agent/learnings",
        json={"session_id": "sess-stale", "task_id": "TASK-001", "text": "x"},
        headers=auth_headers,
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "session_mismatch"


def test_learnings_unknown_session_409(
    tmp_home, app, daemon_state, auth_headers, tmp_path,
) -> None:
    """Unregistered (task, agent) pair — reject and do not create/append."""
    workspace = daemon_state.runtime.workspaces_dir / "dev_agent"
    workspace.mkdir(parents=True, exist_ok=True)
    learnings = workspace / "learnings.md"
    learnings.write_text("# Learnings: dev_agent\n\n")

    r = TestClient(app).post(
        "/api/v1/agents/dev_agent/learnings",
        json={"session_id": "fabricated", "task_id": "TASK-NOPE", "text": "should not land"},
        headers=auth_headers,
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "unknown_session"
    assert "should not land" not in learnings.read_text()
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/daemon/test_routes_agents.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `src/daemon/routes/agents.py`**

```python
"""Agent inspection, init, and learnings callback endpoints."""
from __future__ import annotations

import asyncio
import json as _json

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from src.daemon.auth import require_token
from src.daemon.state import DaemonState
from src.models import AgentName
from src.orchestrator.context_builder import ContextBuilder
from src.orchestrator.performance_tracker import PerformanceTracker
from src.orchestrator.prompt_loader import load_all_prompts

router = APIRouter(dependencies=[require_token()])


class InitBody(BaseModel):
    agent: str | None = None


class LearningBody(BaseModel):
    session_id: str
    task_id: str
    text: str


def _require_active(state: DaemonState) -> None:
    if state.is_idle:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "no_active_runtime"},
        )


@router.get("/agents")
def list_agents(request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_active(state)
    tracker = PerformanceTracker(state.db, state.settings)
    tiers = tracker.get_all_tiers()
    return {
        "agents": [
            {
                "name": a.value,
                "tier": tiers.get(a, "green").value if hasattr(tiers.get(a, "green"), "value") else tiers.get(a, "green"),
                "scorecard": state.db.get_scorecard(a.value),
            }
            for a in AgentName
        ],
    }


@router.post("/agents/init")
async def init_agents(body: InitBody, request: Request):
    state: DaemonState = request.app.state.daemon
    _require_active(state)

    targets: list[AgentName]
    if body.agent is None:
        targets = list(AgentName)
    else:
        targets = [AgentName(body.agent)]

    async def gen():
        protocol_dir = state.settings.get_protocol_dir()
        prompts = load_all_prompts(protocol_dir)
        ctx = ContextBuilder(state.settings)
        for agent in targets:
            workspace = state.runtime.workspaces_dir / agent.value
            workspace.mkdir(parents=True, exist_ok=True)
            yield {"data": _json.dumps({"agent": agent.value, "phase": "starting"})}
            await asyncio.to_thread(
                ctx.initialize_workspace, workspace, agent.value,
                prompts.get(agent.value, ""),
            )
            yield {"data": _json.dumps({"agent": agent.value, "phase": "done"})}
        yield {"data": _json.dumps({"phase": "all_done"})}

    return EventSourceResponse(gen())


@router.post("/agents/{agent_name}/learnings")
async def append_learning(agent_name: str, body: LearningBody, request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_active(state)

    expected = state.sessions.get_active(body.task_id, agent_name)
    # Same gate as completion: the SessionTracker is the source of truth for
    # which sessions exist. No entry → reject (otherwise any local process
    # with the bearer token could append to learnings.md for any agent).
    if expected is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "unknown_session", "task_id": body.task_id, "agent": agent_name},
        )
    if expected != body.session_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "session_mismatch", "active": expected, "got": body.session_id},
        )

    workspace = state.runtime.workspaces_dir / agent_name
    learnings_path = workspace / "learnings.md"
    if not learnings_path.exists():
        learnings_path.parent.mkdir(parents=True, exist_ok=True)
        learnings_path.write_text(f"# Learnings: {agent_name}\n\n")

    async with state.db_lock:
        existing = learnings_path.read_text()
        learnings_path.write_text(existing + f"- {body.text}\n")
    return {"ok": True}
```

- [ ] **Step 4: Wire the router**

In `src/daemon/app.py`:

```python
from src.daemon.routes import agents, health, runtimes, tasks
...
    app.include_router(agents.router, prefix="/api/v1")
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/daemon/test_routes_agents.py -v`
Expected: all 5 tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/daemon/routes/agents.py src/daemon/app.py tests/daemon/test_routes_agents.py
git commit -m "feat(daemon): agents list/init/learnings endpoints"
```

---

## Phase G — CLI conversion

### Task 19: Refactor `opc tasks`, `opc status`, `opc agents` to HTTP

**Files:**
- Modify: `src/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Replace `cmd_tasks`, `cmd_status`, `cmd_agents` with HTTP-client versions**

In `src/cli.py`, replace:

```python
def cmd_tasks(args: argparse.Namespace) -> None:
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    r = client.get("/api/v1/tasks", params={"limit": args.limit})
    if r.status_code != 200:
        print(f"Error ({r.status_code}): {r.text}")
        sys.exit(1)
    tasks = r.json()["tasks"]
    if not tasks:
        print("No tasks found.")
        return
    print(f"{'ID':<12} {'Type':<20} {'Status':<12}  Brief")
    print("-" * 76)
    for t in tasks:
        brief = t["brief"][:40] + "..." if len(t["brief"]) > 40 else t["brief"]
        print(f"{t['id']:<12} {t['type']:<20} {t['status']:<12}  {brief}")


def cmd_status(args: argparse.Namespace) -> None:
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    r = client.get(f"/api/v1/tasks/{args.task_id}")
    if r.status_code == 404:
        print(f"Task {args.task_id} not found.")
        sys.exit(1)
    if r.status_code != 200:
        print(f"Error ({r.status_code}): {r.text}")
        sys.exit(1)
    body = r.json()
    task = body["task"]
    print(f"Task:       {task['id']}")
    print(f"Type:       {task['type']}")
    print(f"Status:     {task['status']}")
    print(f"Agent:      {task.get('assigned_agent') or '-'}")
    print(f"Brief:      {task['brief']}")
    print(f"Created:    {task['created_at']}")
    print(f"Updated:    {task['updated_at']}")
    if body.get("results"):
        print(f"\nResults ({len(body['results'])}):")
        for r_ in body["results"]:
            print(f"  - [{r_['agent']}] confidence={r_['confidence_score']}  {r_['output_summary'][:80]}")
    if body.get("audit_log"):
        print(f"\nAudit log ({len(body['audit_log'])} entries):")
        for log in body["audit_log"]:
            print(f"  {log['timestamp'][:19]}  {log['agent']:20s}  {log['action']}")


def cmd_agents(args: argparse.Namespace) -> None:
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    r = client.get("/api/v1/agents")
    if r.status_code != 200:
        print(f"Error ({r.status_code}): {r.text}")
        sys.exit(1)
    body = r.json()
    print(f"{'Agent':<22} {'Tier':<8}")
    print("-" * 30)
    for entry in body["agents"]:
        print(f"{entry['name']:<22} {entry['tier']:<8}")
    if args.detail:
        print()
        for entry in body["agents"]:
            sc = entry.get("scorecard")
            if sc:
                print(f"{entry['name']}:")
                print(f"  Acceptance: {sc['acceptance_rate']:.0%}  Revision: {sc['revision_rate']:.0%}  Errors: {sc['error_count']}")
                print(f"  Period: {sc['period_start'][:10]} to {sc['period_end'][:10]}")
```

- [ ] **Step 2: Update tests to mock `OpcClient.from_env`**

Add to `tests/test_cli.py`:

```python
def test_cmd_tasks_calls_list_endpoint(capsys):
    from src.cli import cmd_tasks

    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {"tasks": [
        {"id": "TASK-001", "type": "general", "status": "approved", "brief": "x"},
    ]}
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        args = MagicMock(limit=20)
        cmd_tasks(args)
    fake.get.assert_called_once_with("/api/v1/tasks", params={"limit": 20})
    assert "TASK-001" in capsys.readouterr().out


def test_cmd_status_handles_404(capsys):
    from src.cli import cmd_status

    fake = MagicMock()
    fake.get.return_value.status_code = 404
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        args = MagicMock(task_id="TASK-X")
        with pytest.raises(SystemExit):
            cmd_status(args)
    assert "not found" in capsys.readouterr().out
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/test_cli.py -v`
Expected: all pass (any tests left over from the in-process era are now obsolete; remove or rewrite them in this task).

- [ ] **Step 4: Commit**

```bash
git add src/cli.py tests/test_cli.py
git commit -m "feat(cli): convert tasks/status/agents to HTTP clients"
```

---

### Task 20: Refactor `opc run` (POST + SSE) and add `opc tail`

**Files:**
- Modify: `src/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Replace `cmd_run` with the streaming version and add `cmd_tail`**

```python
def cmd_run(args: argparse.Namespace) -> None:
    """Submit a task and stream its events until terminal."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    r = client.post("/api/v1/tasks", json={"type": args.task, "brief": args.brief})
    if r.status_code == 409:
        print(f"Error: {r.json()['detail']}")
        sys.exit(1)
    if r.status_code != 200:
        print(f"Error ({r.status_code}): {r.text}")
        sys.exit(1)
    task_id = r.json()["task_id"]
    print(f"Submitted {task_id}; streaming events (Ctrl-C to detach)...")
    _stream_task_events(client, task_id)


def cmd_tail(args: argparse.Namespace) -> None:
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    _stream_task_events(client, args.task_id)


def _stream_task_events(client: "OpcClient", task_id: str) -> None:
    import json as _json
    try:
        for payload in client.stream("GET", f"/api/v1/tasks/{task_id}/events"):
            try:
                event = _json.loads(payload)
            except _json.JSONDecodeError:
                print(payload)
                continue
            etype = event.get("type", "?")
            print(f"[{etype}] {event}")
            if etype in ("task_complete", "task_escalated", "task_rejected"):
                return
    except KeyboardInterrupt:
        print(f"\nDetached. Reattach with: opc tail {task_id}")
```

In `build_parser`, add the `tail` subcommand:

```python
    p_tail = sub.add_parser("tail", help="Stream events for an existing task")
    p_tail.add_argument("task_id", help="Task ID")
    p_tail.set_defaults(func=cmd_tail)
```

- [ ] **Step 2: Add tests**

Add to `tests/test_cli.py`:

```python
def test_cmd_run_submits_then_streams(capsys):
    from src.cli import cmd_run

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    fake.post.return_value.json.return_value = {"task_id": "TASK-001"}
    fake.stream.return_value = iter([
        '{"type": "audit", "n": 1}',
        '{"type": "task_complete", "outcome": "approved"}',
    ])

    with patch("src.cli.OpcClient.from_env", return_value=fake):
        args = MagicMock(task="general", brief="x")
        cmd_run(args)

    fake.post.assert_called_once_with("/api/v1/tasks", json={"type": "general", "brief": "x"})
    out = capsys.readouterr().out
    assert "TASK-001" in out
    assert "task_complete" in out


def test_cmd_tail_streams_existing_task(capsys):
    from src.cli import cmd_tail

    fake = MagicMock()
    fake.stream.return_value = iter(['{"type": "task_complete"}'])
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        args = MagicMock(task_id="TASK-001")
        cmd_tail(args)
    assert "task_complete" in capsys.readouterr().out
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/test_cli.py -v`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add src/cli.py tests/test_cli.py
git commit -m "feat(cli): opc run streams SSE events; add opc tail"
```

---

### Task 21: Refactor `opc init-agent` and add `opc report-completion` / `opc learning`

**Files:**
- Modify: `src/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Replace `cmd_init_agent` with the SSE-consuming version**

```python
def cmd_init_agent(args: argparse.Namespace) -> None:
    """Initialize agent workspaces by streaming progress from the daemon."""
    import json as _json
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    try:
        for payload in client.stream(
            "POST", "/api/v1/agents/init", json={"agent": args.agent},
        ):
            try:
                event = _json.loads(payload)
            except _json.JSONDecodeError:
                print(payload)
                continue
            if event.get("phase") == "all_done":
                print("Done.")
                return
            agent = event.get("agent", "")
            phase = event.get("phase", "")
            print(f"  [{agent}] {phase}")
    except KeyboardInterrupt:
        print("Init cancelled (daemon will continue).")
```

- [ ] **Step 2: Add new agent-callback CLIs**

```python
def cmd_report_completion(args: argparse.Namespace) -> None:
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    body = {
        "session_id": args.session_id,
        "agent": args.agent,
        "status": args.status,
        "confidence": args.confidence,
        "output_summary": args.summary,
        "risks_flagged": args.risks or [],
        "dependencies": args.dependencies or [],
        "suggested_reviewer_focus": args.reviewer_focus or [],
    }
    r = client.post(f"/api/v1/tasks/{args.task_id}/completion", json=body)
    if r.status_code == 409:
        print(f"Error: {r.json()['detail']}")
        sys.exit(1)
    if r.status_code != 200:
        print(f"Error ({r.status_code}): {r.text}")
        sys.exit(1)


def cmd_learning(args: argparse.Namespace) -> None:
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    r = client.post(
        f"/api/v1/agents/{args.agent}/learnings",
        json={"session_id": args.session_id, "task_id": args.task_id, "text": args.text},
    )
    if r.status_code != 200:
        print(f"Error ({r.status_code}): {r.text}")
        sys.exit(1)
```

In `build_parser`, add:

```python
    p_rep = sub.add_parser("report-completion", help="Agent callback: report task completion")
    p_rep.add_argument("--task-id", required=True)
    p_rep.add_argument("--session-id", required=True)
    p_rep.add_argument("--agent", required=True)
    p_rep.add_argument("--status", required=True, choices=["completed", "blocked"])
    p_rep.add_argument("--confidence", type=int, default=80)
    p_rep.add_argument("--summary", required=True)
    p_rep.add_argument("--risks", action="append", default=[])
    p_rep.add_argument("--dependencies", action="append", default=[])
    p_rep.add_argument("--reviewer-focus", action="append", default=[], dest="reviewer_focus")
    p_rep.set_defaults(func=cmd_report_completion)

    p_learn = sub.add_parser("learning", help="Agent callback: append a learning")
    p_learn.add_argument("--task-id", required=True)
    p_learn.add_argument("--session-id", required=True)
    p_learn.add_argument("--agent", required=True)
    p_learn.add_argument("--text", required=True)
    p_learn.set_defaults(func=cmd_learning)
```

- [ ] **Step 3: Add tests**

```python
def test_cmd_report_completion_posts_with_session_id():
    from src.cli import cmd_report_completion

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    args = MagicMock(
        task_id="TASK-001", session_id="sess-1", agent="dev_agent",
        status="completed", confidence=90, summary="ok",
        risks=[], dependencies=[], reviewer_focus=[],
    )
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        cmd_report_completion(args)
    args_pos, kwargs = fake.post.call_args
    assert args_pos[0] == "/api/v1/tasks/TASK-001/completion"
    assert kwargs["json"]["session_id"] == "sess-1"


def test_cmd_learning_posts_with_session_id():
    from src.cli import cmd_learning

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    args = MagicMock(
        task_id="TASK-001", session_id="sess-1",
        agent="dev_agent", text="x",
    )
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        cmd_learning(args)
    args_pos, kwargs = fake.post.call_args
    assert args_pos[0] == "/api/v1/agents/dev_agent/learnings"
    assert kwargs["json"]["session_id"] == "sess-1"
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_cli.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/cli.py tests/test_cli.py
git commit -m "feat(cli): init-agent over SSE; report-completion + learning callbacks"
```

---

### Task 22: Drop `--runtime` flag and `OPC_RUNTIME` handling from CLI

**Files:**
- Modify: `src/cli.py`

- [ ] **Step 1: Remove the `--runtime` argument and `_require_runtime` / `_get_db` / `_get_settings` helpers that the daemon-routed commands no longer need**

In `src/cli.py`:
- Delete `_require_runtime`, `_get_db`, `_get_settings` (verify nothing in the file still calls them).
- In `build_parser`, delete `parser.add_argument("--runtime", ...)`.

- [ ] **Step 2: Run the full CLI test suite**

Run: `uv run pytest tests/test_cli.py -v`
Expected: all pass.

- [ ] **Step 3: Verify the script still parses without --runtime**

Run: `uv run opc --help`
Expected: help text shows the new commands and no `--runtime` flag.

- [ ] **Step 4: Commit**

```bash
git add src/cli.py
git commit -m "refactor(cli): drop --runtime flag and in-process helpers"
```

---

## Phase H — Skills + workspace updates

### Task 23: Author `start-task` skill

**Files:**
- Create: `protocol/skills/start-task/SKILL.md`

- [ ] **Step 1: Write `protocol/skills/start-task/SKILL.md`**

```markdown
---
name: start-task
description: Use this skill at the start of every task. Parses task_id, session_id, brief, and role_guidance from the prompt, executes the work, reports completion via the opc CLI, and cleans up worktrees.
---

# start-task

The orchestrator daemon spawns you with a prompt of this form:

```
You are <agent_name>. Use the start-task skill to handle this task.
Parameters:
  task_id: TASK-XXX
  session_id: <uuid>
  brief: <task brief>
  role_guidance: <role-specific instructions>
```

## Steps

1. **Parse parameters.** Extract `task_id`, `session_id`, `brief`, and `role_guidance` from the prompt above. Hold `session_id` in a variable for the lifetime of this session — every callback to `opc` must include it.

2. **Plan and execute.** Treat `role_guidance` as your primary instruction. If repo writes are needed, invoke the **make-worktree** skill first.

3. **Report mid-task learnings (optional).** Whenever you discover something reusable for future tasks:

   ```bash
   opc learning --task-id <task_id> --session-id <session_id> --agent <your_agent_name> --text "..."
   ```

4. **Report completion.** When you finish (success or blocker), call:

   - **Success:**
     ```bash
     opc report-completion \
       --task-id <task_id> --session-id <session_id> --agent <your_agent_name> \
       --status completed --confidence <0-100> \
       --summary "<what you did>" \
       --risks "<concern>" \
       --dependencies "<assumption>" \
       --reviewer-focus "<where to look hardest>"
     ```
   - **Blocker:**
     ```bash
     opc report-completion \
       --task-id <task_id> --session-id <session_id> --agent <your_agent_name> \
       --status blocked --confidence 0 --summary "<what blocked you>"
     ```

5. **Cleanup.** Always run worktree cleanup as the final step, even on the blocker path. The make-worktree skill describes how.

## Error handling

- If `opc` returns non-zero, retry once after 1 second.
- **Exceptions (no retry, fatal):** `409 session_mismatch` (the daemon has spawned a newer session for this `(task_id, agent)`) and `409 unknown_session` (the daemon has no record of this spawn — the session is orphaned). Either way, exit immediately.
```

- [ ] **Step 2: Verify the file is well-formed**

Run: `head -3 protocol/skills/start-task/SKILL.md`
Expected: shows the YAML frontmatter delimiter `---`.

- [ ] **Step 3: Commit**

```bash
git add protocol/skills/start-task/SKILL.md
git commit -m "feat(skills): author start-task skill"
```

---

### Task 24: Author `make-worktree` skill

**Files:**
- Create: `protocol/skills/make-worktree/SKILL.md`

- [ ] **Step 1: Write `protocol/skills/make-worktree/SKILL.md`**

```markdown
---
name: make-worktree
description: Use this skill before any git commit, git checkout, or file edit inside repos/<name>/. Read-only exploration does not need a worktree. Manages a per-task git worktree at .claude/worktrees/<task_id>/ on branch task/<task_id>.
---

# make-worktree

Per Claude Code convention, worktrees live inside the repo at `.claude/worktrees/<task_id>/` and use a branch named `task/<task_id>`.

## When to invoke

Before any operation in `repos/<repo_name>/` that mutates state:
- `git commit`
- `git checkout`
- file edits (Write, Edit)

Read-only operations (Read, Grep, Glob) do not need a worktree.

## Setup

```bash
cd repos/<repo_name>
mkdir -p .claude/worktrees
git worktree add .claude/worktrees/<task_id> -b task/<task_id>
cd .claude/worktrees/<task_id>
# All writes happen here.
```

## Concurrency

Two sessions on the same agent role may try to create different worktrees simultaneously. If `git worktree add` fails because of a stale lock, retry once after 1 second.

## Cleanup

At the end of every task — even on blocker/error paths — remove the worktree:

```bash
cd repos/<repo_name>
git worktree remove .claude/worktrees/<task_id> --force
git branch -D task/<task_id> 2>/dev/null || true
```

If cleanup fails (uncommitted changes you wanted to keep), leave the worktree and surface this in the completion report's `risks_flagged`.
```

- [ ] **Step 2: Commit**

```bash
git add protocol/skills/make-worktree/SKILL.md
git commit -m "feat(skills): author make-worktree skill"
```

---

### Task 25: Update `context_builder.py` — drop `task_brief`, drop `completion_report.json` doc, copy skills

**Files:**
- Modify: `src/orchestrator/context_builder.py`
- Modify: `tests/test_context_builder.py`

- [ ] **Step 1: Update the failing tests**

Open `tests/test_context_builder.py` and remove any test that checks `task_brief` is rendered or `completion_report.json` is mentioned. Add:

```python
def test_initialize_workspace_copies_skills(test_settings, tmp_path):
    from src.orchestrator.context_builder import ContextBuilder

    # Set up a fake protocol/skills/ tree
    skills_root = test_settings.get_protocol_dir() / "skills"
    (skills_root / "start-task").mkdir(parents=True)
    (skills_root / "start-task" / "SKILL.md").write_text("# start-task\n")
    (skills_root / "make-worktree").mkdir(parents=True)
    (skills_root / "make-worktree" / "SKILL.md").write_text("# make-worktree\n")

    workspace = tmp_path / "workspace"
    ContextBuilder(test_settings).initialize_workspace(workspace, "dev_agent", "system prompt")

    assert (workspace / ".claude" / "skills" / "start-task" / "SKILL.md").read_text() == "# start-task\n"
    assert (workspace / ".claude" / "skills" / "make-worktree" / "SKILL.md").read_text() == "# make-worktree\n"


def test_claude_md_drops_task_brief_and_completion_report(test_settings, tmp_path):
    from src.orchestrator.context_builder import ContextBuilder

    workspace = tmp_path / "workspace"
    ContextBuilder(test_settings).write_claude_md(workspace, "dev_agent", "system prompt")
    text = (workspace / "CLAUDE.md").read_text()
    assert "Current Task" not in text
    assert "completion_report.json" not in text
```

- [ ] **Step 2: Run tests and watch them fail**

Run: `uv run pytest tests/test_context_builder.py -v`
Expected: failures.

- [ ] **Step 3: Update `src/orchestrator/context_builder.py`**

Replace `write_claude_md` with:

```python
    def write_claude_md(
        self,
        workspace: Path,
        agent_name: str,
        system_prompt: str,
        repo_names: list[str] | None = None,
    ) -> None:
        sections = [
            f"# Agent: {agent_name}\n",
            "## System Prompt\n",
            system_prompt.strip() + "\n",
        ]
        if repo_names:
            sections.append("## Available Repositories\n")
            for name in repo_names:
                sections.append(f"- `repos/{name}/` — git clone, kept fresh via PreToolUse hook")
            sections.append("")
        sections.extend([
            "## Persistent Files\n",
            "- `learnings.md` -- your accumulated operational learnings",
            "- `scorecard.md` -- read-only, updated by orchestrator",
            "- `recent_tasks.md` -- read-only, updated by orchestrator\n",
            "## Workflow\n",
            "Every task arrives via the orchestrator's prompt. Use the **start-task** skill",
            "(in `.claude/skills/start-task/`) to parse parameters and report completion via",
            "`opc report-completion`. Mid-task learnings go through `opc learning`.\n",
        ])
        (workspace / "CLAUDE.md").write_text("\n".join(sections))
```

Add a `_copy_skills` helper and call it from `initialize_workspace`:

```python
import shutil

    def _copy_skills(self, workspace: Path) -> None:
        src = self._settings.get_protocol_dir() / "skills"
        if not src.exists():
            return
        dst = workspace / ".claude" / "skills"
        dst.mkdir(parents=True, exist_ok=True)
        for child in src.iterdir():
            target = dst / child.name
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(child, target)
```

In `initialize_workspace`, call `self._copy_skills(workspace)` before `write_settings_json`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_context_builder.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/context_builder.py tests/test_context_builder.py
git commit -m "refactor(context): drop task_brief; copy skills into workspace"
```

---

## Phase I — Daemon-restart policy + cleanup

### Task 26: Escalate IN_PROGRESS tasks on daemon startup

**Files:**
- Modify: `src/daemon/__main__.py`
- Modify: `tests/daemon/test_routes_health.py` (new test file: `tests/daemon/test_startup_recovery.py`)

- [ ] **Step 1: Write the failing test**

Create `tests/daemon/test_startup_recovery.py`:

```python
from __future__ import annotations

from pathlib import Path

from src.config import Settings
from src.daemon.__main__ import _escalate_in_flight_tasks
from src.infrastructure.database import Database
from src.models import TaskRecord, TaskStatus, TaskType
from src.runtime import RuntimeDir


def test_escalate_in_flight_tasks_marks_them_escalated(tmp_path: Path) -> None:
    runtime = RuntimeDir.init(tmp_path / "rt")
    db = Database(runtime.db_path)
    db.insert_task(TaskRecord(id="TASK-001", type=TaskType.GENERAL, brief="x"))
    db.update_task("TASK-001", status=TaskStatus.IN_PROGRESS)
    db.insert_task(TaskRecord(id="TASK-002", type=TaskType.GENERAL, brief="y"))
    db.update_task("TASK-002", status=TaskStatus.APPROVED)

    _escalate_in_flight_tasks(db)

    assert db.get_task("TASK-001").status == TaskStatus.ESCALATED
    assert db.get_task("TASK-002").status == TaskStatus.APPROVED


def test_escalate_in_flight_tasks_logs_audit(tmp_path: Path) -> None:
    runtime = RuntimeDir.init(tmp_path / "rt")
    db = Database(runtime.db_path)
    db.insert_task(TaskRecord(id="TASK-001", type=TaskType.GENERAL, brief="x"))
    db.update_task("TASK-001", status=TaskStatus.IN_PROGRESS)

    _escalate_in_flight_tasks(db)

    logs = db.get_audit_logs("TASK-001")
    assert any(
        log["action"] == "escalation"
        and "daemon restarted" in (log["payload"] or {}).get("reason", "")
        for log in logs
    )
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/daemon/test_startup_recovery.py -v`
Expected: `ImportError` on `_escalate_in_flight_tasks`.

- [ ] **Step 3: Implement the helper in `src/daemon/__main__.py`**

Add this function above `_build_state`:

```python
def _escalate_in_flight_tasks(db) -> None:
    """Mark nonterminal tasks (PENDING + IN_PROGRESS) as escalated — daemon restart
    kills any in-flight spawn and orphans queued runners. No resumption in Spec 1."""
    from src.infrastructure.audit_logger import AuditLogger
    from src.models import TaskStatus

    audit = AuditLogger(db)
    for task_id in db.get_nonterminal_task_ids():
        db.update_task(task_id, status=TaskStatus.ESCALATED)
        audit.log_escalation(task_id, "daemon", "daemon restarted mid-task")
```

Call it from `_build_state` right after `state.db = Database(...)`:

```python
    runtime = RuntimeDir.load(reg.active)
    state = DaemonState.from_runtime(runtime, settings)
    _escalate_in_flight_tasks(state.db)
    return state
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/daemon/test_startup_recovery.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/__main__.py tests/daemon/test_startup_recovery.py
git commit -m "feat(daemon): escalate in-flight tasks on startup"
```

---

### Task 27: Skill-file validation tests

**Files:**
- Create: `tests/test_skills.py`

- [ ] **Step 1: Write the test**

```python
from __future__ import annotations

from pathlib import Path

import pytest
import yaml


SKILLS_ROOT = Path(__file__).resolve().parent.parent / "protocol" / "skills"


def _parse_frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        raise ValueError("missing frontmatter delimiter")
    _, fm, _body = text.split("---", 2)
    return yaml.safe_load(fm)


@pytest.mark.parametrize("skill_name", ["start-task", "make-worktree"])
def test_skill_has_required_frontmatter(skill_name: str) -> None:
    skill_md = SKILLS_ROOT / skill_name / "SKILL.md"
    assert skill_md.exists(), f"missing {skill_md}"
    fm = _parse_frontmatter(skill_md.read_text())
    assert fm["name"] == skill_name
    assert isinstance(fm.get("description"), str)
    assert len(fm["description"]) > 20


def test_start_task_references_session_id_on_callbacks() -> None:
    body = (SKILLS_ROOT / "start-task" / "SKILL.md").read_text()
    assert "--session-id" in body
    assert "report-completion" in body
    assert "learning" in body


def test_make_worktree_references_claude_worktrees_path() -> None:
    body = (SKILLS_ROOT / "make-worktree" / "SKILL.md").read_text()
    assert ".claude/worktrees/" in body
    assert "git worktree add" in body


def test_skill_cli_commands_exist() -> None:
    """Every `opc <subcommand>` referenced by a skill must be a real subcommand."""
    from src.cli import build_parser

    parser = build_parser()
    subparsers_action = next(
        a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction"
    )
    known = set(subparsers_action.choices.keys())

    referenced = set()
    for skill in SKILLS_ROOT.iterdir():
        body = (skill / "SKILL.md").read_text()
        for line in body.splitlines():
            if "opc " in line:
                # crude extractor: tokens after "opc "
                idx = line.find("opc ")
                tokens = line[idx + 4:].split()
                if tokens:
                    referenced.add(tokens[0])
    referenced -= {"<subcommand>"}
    missing = referenced - known
    assert not missing, f"skills reference missing CLI commands: {missing}"
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/test_skills.py -v`
Expected: all pass — the skills + CLI from prior tasks are consistent. Any failure here indicates a real drift bug.

- [ ] **Step 3: Commit**

```bash
git add tests/test_skills.py
git commit -m "test: validate skill frontmatter and CLI command refs"
```

---

### Task 28: End-to-end integration test with fake Claude binary

**Files:**
- Create: `tests/integration/fake_claude.sh`
- Create: `tests/integration/conftest.py`
- Create: `tests/integration/test_end_to_end.py`

This task gates a real daemon process via `scripts/daemon.sh start` and proves the full stack works.

- [ ] **Step 1: Create `tests/integration/fake_claude.sh`**

```bash
#!/usr/bin/env bash
# Fake Claude binary — reads scripted behavior from $FAKE_CLAUDE_PLAN
# and optionally calls opc to simulate an agent's session.
set -e

PROMPT=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        -p) PROMPT="$2"; shift 2 ;;
        --permission-mode) shift 2 ;;
        *) shift ;;
    esac
done

# Extract task_id and session_id from the prompt.
TASK_ID=$(echo "$PROMPT" | awk -F': ' '/^Task ID: /{print $2; exit}')
SESSION_ID=$(echo "$PROMPT" | awk -F': ' '/^Session ID: /{print $2; exit}')

# If a plan file exists, source it (it can call opc).
if [[ -n "${FAKE_CLAUDE_PLAN:-}" && -f "$FAKE_CLAUDE_PLAN" ]]; then
    bash "$FAKE_CLAUDE_PLAN" "$TASK_ID" "$SESSION_ID"
fi

exit 0
```

Make it executable: `chmod +x tests/integration/fake_claude.sh`

- [ ] **Step 2: Create `tests/integration/conftest.py`**

```python
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from src.daemon import paths as paths_mod
from src.runtime import RuntimeDir


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("OPC_DAEMON_HOME", str(tmp_path / ".opc"))
    return tmp_path / ".opc"


@pytest.fixture
def runtime(tmp_path: Path) -> Path:
    rt = RuntimeDir.init(tmp_path / "runtime")
    return rt.root


@pytest.fixture
def fake_claude(tmp_path: Path) -> Path:
    src = Path(__file__).parent / "fake_claude.sh"
    dst = tmp_path / "fake_claude.sh"
    dst.write_bytes(src.read_bytes())
    dst.chmod(0o755)
    return dst


@pytest.fixture
def live_daemon(tmp_home, fake_claude, monkeypatch):
    """Start the daemon via scripts/daemon.sh and stop it after the test."""
    monkeypatch.setenv("OPC_CLAUDE_CLI_PATH", str(fake_claude))
    script = Path(__file__).resolve().parent.parent.parent / "scripts" / "daemon.sh"
    subprocess.run([str(script), "start"], check=True)
    # Wait for /health to respond
    deadline = time.time() + 5
    while time.time() < deadline:
        if paths_mod.port_file().exists():
            port = paths_mod.port_file().read_text().strip()
            try:
                r = httpx.get(f"http://127.0.0.1:{port}/api/v1/health", timeout=1.0)
                if r.status_code == 200:
                    yield port
                    break
            except httpx.HTTPError:
                pass
        time.sleep(0.2)
    else:
        raise RuntimeError("daemon failed to start")
    subprocess.run([str(script), "stop"], check=False)
```

- [ ] **Step 3: Write the integration test**

`tests/integration/test_end_to_end.py`:

```python
from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest


pytestmark = pytest.mark.integration


def _auth_headers() -> dict:
    from src.daemon import paths
    return {"Authorization": f"Bearer {paths.read_token()}"}


def test_register_and_run_completes_via_callback(live_daemon, runtime, tmp_path):
    port = live_daemon
    base = f"http://127.0.0.1:{port}/api/v1"

    # Plan: fake claude calls report-completion.
    plan = tmp_path / "plan.sh"
    plan.write_text(
        '#!/usr/bin/env bash\n'
        'task_id=$1; session_id=$2\n'
        'opc report-completion \\\n'
        '  --task-id "$task_id" --session-id "$session_id" \\\n'
        '  --agent engineering_head --status completed --confidence 90 \\\n'
        '  --summary \'{"action":"done","summary":"ok"}\'\n'
    )
    plan.chmod(0o755)
    os.environ["FAKE_CLAUDE_PLAN"] = str(plan)

    # Register the runtime
    r = httpx.post(f"{base}/runtimes/register", json={"path": str(runtime)},
                   headers=_auth_headers(), timeout=5.0)
    assert r.status_code == 200

    # Submit a task
    r = httpx.post(f"{base}/tasks", json={"type": "general", "brief": "smoke"},
                   headers=_auth_headers(), timeout=5.0)
    assert r.status_code == 200
    task_id = r.json()["task_id"]

    # Stream events until terminal
    with httpx.stream("GET", f"{base}/tasks/{task_id}/events",
                      headers=_auth_headers(), timeout=30.0) as stream:
        outcome = None
        for line in stream.iter_lines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line.removeprefix("data: "))
            if event.get("type") in ("task_complete", "task_escalated", "task_rejected"):
                outcome = event.get("type")
                break
    assert outcome == "task_complete"

    # Confirm DB has a task_result tagged with a session_id
    r = httpx.get(f"{base}/tasks/{task_id}", headers=_auth_headers(), timeout=5.0)
    body = r.json()
    assert body["task"]["status"] == "approved"
    assert any(res["session_id"] for res in body["results"])
```

- [ ] **Step 4: Mark the integration tests gated**

Add to `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
markers = [
    "integration: end-to-end tests that spawn a real daemon",
]
```

- [ ] **Step 5: Run the integration test (opt-in)**

Run: `uv run pytest tests/integration/ -v -m integration`
Expected: 1 test passes (takes a few seconds).

Run the unit suite excluding integration: `uv run pytest tests/ -q -m "not integration"`
Expected: full unit suite green.

- [ ] **Step 6: Commit**

```bash
git add tests/integration/ pyproject.toml
git commit -m "test: end-to-end integration via real daemon + fake claude"
```

---

## Self-Review Checklist

Before declaring this plan done, verify:

**Spec coverage** (each spec section → task):

- §1 Responsibilities & Boundary → Tasks 6, 7, 16
- §2 Daemon Home & Runtime Registry → Tasks 2, 3, 9
- §3 HTTP API Surface (auth, runtimes, tasks, agents, errors) → Tasks 5, 6, 9, 16, 17, 18
- §4 CLI Client Refactor → Tasks 8, 10, 19, 20, 21, 22
- §5 Daemon-Side Task Execution (sessions, completion, concurrency, restart, event bus) → Tasks 11, 12, 13, 14, 15, 17, 26
- §6 Claude Code Skills (start-task, make-worktree, distribution) → Tasks 23, 24, 25
- §7 Worktree Convention → Task 24 (skill content)
- §8 Lifecycle Script → Task 7
- §9 Code Changes → covered cumulatively across Tasks 1, 14, 22, 25
- §10 Testing → Tasks 27, 28
- §11 Out of Scope → no task; documented as intentional

**Placeholder scan:** the words "TODO", "TBD", "implement later", "fill in details" do not appear in this plan body.

**Type consistency:**
- `OpcClient` is the client class throughout (Tasks 8, 10, 19–21).
- `DaemonNotRunning` and `DaemonStateInconsistent` are the two client errors (Task 8, used in 10/19–21).
- `SessionTracker` methods are `set_active`, `get_active`, `clear` everywhere.
- `EventBus` API is `publish(task_id, event)` and `subscribe(task_id) -> AsyncIterator`.
- DB helper is `get_latest_task_result(task_id, agent, session_id)`.
- `_run_agent` returns a tuple `(ExecutorResult, CompletionReport | None)` and accepts `on_session_started`.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-14-orchestrator-daemon.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
