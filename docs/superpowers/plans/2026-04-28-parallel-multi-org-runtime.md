# Parallel Multi-Org Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the daemon from one-active-runtime to one-runtime-container-with-N-orgs-running-concurrently. After this plan ships, two orgs in the same daemon can submit tasks at the same time and both run to completion in parallel, with structurally impossible cross-org leakage in the DB, KB, audit log, sessions, and event bus.

**Architecture:** Filesystem becomes `<runtime>/orgs/<slug>/{org,workspaces,kb,talks,opc.db}` with `<runtime>/opc.yaml` upgraded to `schema_version: 2, type: multi-org-runtime` (no slug at runtime level). `DaemonState` becomes a registry of `OrgState`s keyed by slug; each `OrgState` owns its own DB connection, teams registry, session tracker, event bus, and asyncio locks. One global `TaskQueue` carries `(slug, task_id)` tuples; N workers pop and dispatch to the right org's `Orchestrator`. Every per-org HTTP route moves under `/api/v1/orgs/<slug>/...` gated by an `OrgDep` FastAPI dependency. Every per-org CLI command takes `--org <slug>` (or auto-infers a single org). Agent-side `opc` callbacks get the slug baked into their skill files at workspace-bootstrap time.

**Tech Stack:** Python 3.11+, `uv`, FastAPI, Pydantic v2, SQLite (WAL), pytest. No new runtime deps.

**Spec reference:** `docs/superpowers/specs/2026-04-28-parallel-multi-org-runtime-design.md` is the authoritative source.

---

## File Structure

### New files

| Path | Responsibility |
|---|---|
| `src/daemon/org_state.py` | `OrgState` dataclass, `load`, `close`, `from_org_root` |
| `src/daemon/routes/orgs.py` | `/api/v1/orgs` (list, init, unload) + `OrgDep` dependency |
| `src/daemon/routes/runtime.py` | `/api/v1/runtime` (info, register, use) — replaces `/api/v1/runtimes/*` |
| `src/daemon/migration_multi_org.py` | `opc migrate-to-multi-org` implementation |
| `tests/daemon/test_org_state.py` | Unit tests for `OrgState` lifecycle and isolation |
| `tests/daemon/test_routes_orgs.py` | `/api/v1/orgs` endpoint coverage |
| `tests/daemon/test_routes_runtime.py` | `/api/v1/runtime` endpoint coverage |
| `tests/test_migration_multi_org.py` | Migration-script unit tests (dry-run, apply, idempotency, refusal cases) |
| `tests/integration/test_two_orgs_concurrent.py` | Two orgs running tasks in parallel through one daemon |
| `tests/integration/test_migration_multi_org_e2e.py` | End-to-end: v1 fixture → migrate → daemon serves migrated org |

### Modified files

| Path | What changes |
|---|---|
| `src/runtime.py` | `RuntimeDir` accepts `schema_version: 2` markers without `slug`; `load` refuses `schema_version: 1` with a "run migrate-to-multi-org" error; new `orgs_dir` and `iter_org_roots` properties; `init` writes v2 markers (no slug); legacy `slug` property removed. |
| `src/daemon/state.py` | `DaemonState` becomes a registry: `orgs: dict[str, OrgState]`, `orgs_lock: asyncio.Lock`, `get_org`, `add_org`, `remove_org`. Drops `db`, `teams`, `db_lock`, `kb_lock`, `teams_lock`, `sessions`, `event_bus` — those move into `OrgState`. `from_runtime` discovers and loads every org under `<runtime>/orgs/`. |
| `src/daemon/queue.py` | `TaskQueue` items become `(slug, task_id)` tuples; `enqueue` and `put_nowait` gain a `slug` parameter; worker loop unpacks the tuple, looks up `state.get_org(slug)`, dispatches to that org's orchestrator. |
| `src/daemon/runner.py` | `enqueue_task(state, slug, task_id)` signature change. |
| `src/daemon/app.py` | Lifespan calls `DaemonState.from_runtime` to load all orgs at startup; `ensure_workers_started` builds **one** worker pool against the global queue (no longer one Orchestrator per app). |
| `src/daemon/routes/tasks.py` | Path prefix `/orgs/{slug}/tasks/...`; reads `org: OrgDep` instead of `state`; replaces `state.db` → `org.db`, `state.event_bus` → `org.event_bus`, etc. |
| `src/daemon/routes/agents.py` | Same as tasks: `/orgs/{slug}/agents/...`, `org: OrgDep`. |
| `src/daemon/routes/audit.py` | Same: `/orgs/{slug}/audit`, `org: OrgDep`. |
| `src/daemon/routes/kb.py` | Same: `/orgs/{slug}/kb/...`. |
| `src/daemon/routes/talks.py` | Same: `/orgs/{slug}/talks/...`. |
| `src/daemon/routes/runtimes.py` | **Deleted.** Replaced by `routes/runtime.py` (singular) + `routes/orgs.py`. |
| `src/orchestrator/orchestrator.py` | `Orchestrator.__init__(db, settings, runtime, teams)` keeps the same signature, but `runtime` now is the **org's `RuntimeDir`-shaped root** (i.e., a `RuntimeDir` view of `<container>/orgs/<slug>/`). Helper `Orchestrator.org_root` is the alias used by run_step, workspace_adapters, etc. |
| `src/orchestrator/workspace_adapters.py` | Skill-copy step substitutes `{ORG_SLUG}` with literal slug; `ClaudeWorkspaceAdapter` and `CodexWorkspaceAdapter` constructors gain a `slug: str` param; `_copy_skills` uses it. |
| `src/orchestrator/migration.py` | Old `migrate_to_org_runtime` stays for legacy paths but is no longer the recommended migration; new code lives in `src/daemon/migration_multi_org.py`. |
| `src/cli.py` | `--org <slug>` resolution helper; new commands `opc orgs`, `opc orgs init`, `opc orgs unload`, `opc runtime`, `opc migrate-to-multi-org`; every per-org command threads `--org` through; every agent-callback command (`opc report-completion`, `opc learning`, `opc manage-repo`, `opc manage-agent`, `opc kb *`, `opc dispatch`, `opc talk *`) gains `--org`. |
| `protocol/skills/start-task/SKILL.md`, `protocol/skills/manage-repo/SKILL.md`, `protocol/skills/manage-agent/SKILL.md`, `protocol/skills/talk/SKILL.md` | Add `--org {ORG_SLUG}` to every example `opc` invocation; the literal `{ORG_SLUG}` gets substituted at workspace-bootstrap time. |
| `tests/conftest.py`, `tests/daemon/conftest.py`, `tests/integration/conftest.py` | New fixtures: `make_container(tmp_path)`, `make_org(container, slug=...)`, `make_org_state(org_root, settings)`. Existing single-runtime fixtures are kept but rebuilt on top of these. |
| `tests/test_runtime.py`, `tests/daemon/test_routes_*.py`, `tests/test_cli.py`, `tests/integration/test_*.py` | Refactor to use new fixtures + path-prefixed URLs; assert isolation properties where relevant. |
| `CLAUDE.md` | Updates to directory layout, CLI, configuration to match the new shape. |

### Deleted files

| Path | Reason |
|---|---|
| `src/daemon/routes/runtimes.py` | Replaced by `routes/runtime.py` (singular) + `routes/orgs.py` |
| `tests/daemon/test_routes_runtimes.py` | Replaced by `test_routes_runtime.py` + `test_routes_orgs.py` |

---

## Conventions used in this plan

- Every code edit ships with a test. Failing test first, implementation second, passing test third, commit fourth.
- File-path lines like `src/foo.py:123-145` indicate the existing range you are modifying. New ranges are noted in the step text.
- "Run: …" lines show the exact command. "Expected: …" shows the expected outcome (PASS / FAIL with specific message).
- Commit boundaries are explicit. Each phase ends with `git status` clean and the full unit suite green.
- Follow the existing project style: `from __future__ import annotations`, type hints, Pydantic v2, no new comments unless they explain WHY.

---

## Phase 1: Schema v2 + Foundation

Goal: introduce the new runtime shape and the `OrgState` building block. No daemon-routing changes yet — the old daemon code path still works because nothing instantiates an `OrgState` yet.

### Task 1: Bump `RuntimeDir` to schema v2

**Files:**
- Modify: `src/runtime.py` (full file — small)
- Modify: `tests/test_runtime.py`

- [ ] **Step 1: Write failing test for v2 marker round-trip**

Add to `tests/test_runtime.py`:

```python
def test_init_writes_v2_marker_without_slug(tmp_path: Path) -> None:
    """Fresh runtime gets schema_version 2 and no slug at the runtime level."""
    rt = RuntimeDir.init(tmp_path / "rt")
    data = yaml.safe_load(rt.marker_file.read_text())
    assert data["schema_version"] == 2
    assert data["type"] == "multi-org-runtime"
    assert "slug" not in data
    assert (rt.root / "orgs").is_dir()


def test_load_refuses_schema_v1(tmp_path: Path) -> None:
    """A v1 marker (with slug) is rejected with a clear migration message."""
    root = tmp_path / "legacy"
    root.mkdir()
    (root / "opc.yaml").write_text(
        "slug: hk-tourism\nschema_version: 1\ncreated_at: 2026-04-01T00:00:00Z\n"
    )
    with pytest.raises(ValueError, match="migrate-to-multi-org"):
        RuntimeDir.load(root)


def test_iter_org_roots_returns_subdirs(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt")
    (rt.orgs_dir / "alpha").mkdir()
    (rt.orgs_dir / "alpha" / "org").mkdir()
    (rt.orgs_dir / "alpha" / "org" / "teams.yaml").write_text("teams: {}\n")
    (rt.orgs_dir / "beta").mkdir()
    (rt.orgs_dir / "beta" / "org").mkdir()
    (rt.orgs_dir / "beta" / "org" / "teams.yaml").write_text("teams: {}\n")
    (rt.orgs_dir / "_pending").mkdir()  # reserved name, must be skipped

    slugs = sorted(slug for slug, _ in rt.iter_org_roots())
    assert slugs == ["alpha", "beta"]
```

- [ ] **Step 2: Run tests, expect failures**

Run: `uv run pytest tests/test_runtime.py::test_init_writes_v2_marker_without_slug tests/test_runtime.py::test_load_refuses_schema_v1 tests/test_runtime.py::test_iter_org_roots_returns_subdirs -v`
Expected: 3 failures (slug still required by `init`, no `iter_org_roots` method, `load` doesn't refuse v1).

- [ ] **Step 3: Replace the slug-based runtime with the v2 container**

Replace the body of `src/runtime.py`:

```python
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import yaml

_RESERVED_ORG_SLUGS = frozenset({"_pending", "_archive"})
_SLUG_RE = re.compile(r"^[a-z0-9-]{1,40}$")


class RuntimeDir:
    """A multi-org runtime container.

    The container itself has no slug — orgs live under ``orgs/<slug>/`` and
    each org's slug is its directory name. The container's ``opc.yaml``
    marker only carries ``schema_version: 2`` and ``type: multi-org-runtime``.
    """

    def __init__(self, path: Path) -> None:
        self._path = path.resolve()

    @property
    def root(self) -> Path:
        return self._path

    @property
    def marker_file(self) -> Path:
        return self._path / "opc.yaml"

    @property
    def orgs_dir(self) -> Path:
        return self._path / "orgs"

    def is_valid(self) -> bool:
        return self.marker_file.exists()

    def iter_org_roots(self) -> Iterator[tuple[str, Path]]:
        """Yield ``(slug, org_root)`` for every valid org subdirectory.

        Reserved names (``_pending``, ``_archive``) are skipped. A directory
        without ``org/teams.yaml`` is treated as not-yet-initialized and
        skipped silently — this is what lets ``opc orgs init`` materialize
        the skeleton lazily.
        """
        if not self.orgs_dir.is_dir():
            return
        for entry in sorted(self.orgs_dir.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name in _RESERVED_ORG_SLUGS:
                continue
            if not _SLUG_RE.match(entry.name):
                continue
            if not (entry / "org" / "teams.yaml").is_file():
                continue
            yield entry.name, entry

    @classmethod
    def init(cls, path: Path) -> RuntimeDir:
        instance = cls(path)
        instance.root.mkdir(parents=True, exist_ok=True)
        instance.orgs_dir.mkdir(parents=True, exist_ok=True)
        if not instance.marker_file.exists():
            instance.marker_file.write_text(yaml.safe_dump({
                "schema_version": 2,
                "type": "multi-org-runtime",
                "created_at": datetime.now(timezone.utc).isoformat().replace(
                    "+00:00", "Z"
                ),
            }, sort_keys=False))
        return instance

    @classmethod
    def load(cls, path: Path) -> RuntimeDir:
        instance = cls(path)
        if not instance.is_valid():
            raise ValueError(
                f"{path} is not a valid OPC runtime directory "
                f"(missing {instance.marker_file})"
            )
        data = yaml.safe_load(instance.marker_file.read_text()) or {}
        version = data.get("schema_version")
        if version != 2:
            raise ValueError(
                f"runtime at {path} is schema_version {version!r} (single-org). "
                f"run `opc migrate-to-multi-org {path} "
                f"--i-have-a-backup --apply` to migrate."
            )
        return instance
```

- [ ] **Step 4: Run failing tests; expect pass**

Run: `uv run pytest tests/test_runtime.py -v`
Expected: PASS for all 3 new tests; older tests in this file may now fail because they expect a slug-bearing runtime — that's fine, they will be deleted in Step 5.

- [ ] **Step 5: Delete superseded tests in `tests/test_runtime.py`**

Remove every test that asserts:
- `RuntimeDir.init(path, slug="...")` with a slug arg succeeds
- `rt.slug` returns a value
- v1 markers (no `schema_version` or `schema_version: 1`) load successfully

Keep tests that assert filesystem skeleton creation (`workspaces/` etc. — note: **these are removed** in this plan because workspaces now live under `<runtime>/orgs/<slug>/workspaces/`, not at the runtime root). Replace any such test with a counterpart in the org-skeleton task in Phase 2.

- [ ] **Step 6: Run the full unit suite to find downstream breakage**

Run: `uv run pytest tests/ -v 2>&1 | tail -60`
Expected: many failures — anywhere code calls `runtime.workspaces_dir`, `runtime.org_dir`, `runtime.agents_dir`, `runtime.slug`, `runtime.teams_config_path`. These callers will all be re-pointed in Phase 2 (org root carries those paths). For now, capture the failing-test list to a scratch note.

- [ ] **Step 7: Commit**

```bash
git add src/runtime.py tests/test_runtime.py
git commit -m "refactor(runtime): bump RuntimeDir to schema v2 (multi-org container)

The container is now slug-less; orgs live under orgs/<slug>/. v1 markers
are refused with a pointer to the migration command. Downstream callers
that read workspaces_dir/org_dir/etc. directly from RuntimeDir will be
re-pointed at the org root in the next task — they are intentionally
broken at this commit."
```

---

### Task 2: Define `OrgState` and helpers

**Files:**
- Create: `src/daemon/org_state.py`
- Create: `tests/daemon/test_org_state.py`
- Modify: `src/orchestrator/teams.py:NN-NN` (TeamsRegistry now takes an `org_root: Path`, not a `RuntimeDir`)

- [ ] **Step 1: Write failing test for `OrgState.load`**

Create `tests/daemon/test_org_state.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from src.config import Settings
from src.daemon.org_state import OrgState


def _seed_org(org_root: Path) -> None:
    org_root.mkdir(parents=True)
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text("teams: {}\n")
    (org_root / "org" / "agents").mkdir()
    (org_root / "workspaces").mkdir()
    (org_root / "kb").mkdir()
    (org_root / "talks").mkdir()


def test_org_state_load_opens_db_and_teams(tmp_path: Path) -> None:
    org_root = tmp_path / "rt" / "orgs" / "alpha"
    _seed_org(org_root)
    settings = Settings()
    org = OrgState.load(slug="alpha", root=org_root, settings=settings)
    assert org.slug == "alpha"
    assert org.root == org_root
    assert org.db is not None
    assert org.teams is not None
    org.close()


def test_org_state_two_orgs_independent_dbs(tmp_path: Path) -> None:
    """Two OrgStates point at distinct DB files — writes don't cross over."""
    rt = tmp_path / "rt"
    a_root = rt / "orgs" / "alpha"
    b_root = rt / "orgs" / "beta"
    _seed_org(a_root)
    _seed_org(b_root)
    settings = Settings()
    org_a = OrgState.load(slug="alpha", root=a_root, settings=settings)
    org_b = OrgState.load(slug="beta", root=b_root, settings=settings)
    a_id = org_a.db.next_task_id()
    b_id = org_b.db.next_task_id()
    assert a_id == "TASK-001"
    assert b_id == "TASK-001"  # independent counters per org
    assert org_a.db.path != org_b.db.path
    org_a.close()
    org_b.close()


def test_org_state_close_releases_db(tmp_path: Path) -> None:
    org_root = tmp_path / "rt" / "orgs" / "alpha"
    _seed_org(org_root)
    settings = Settings()
    org = OrgState.load(slug="alpha", root=org_root, settings=settings)
    org.close()
    with pytest.raises(Exception):
        org.db.next_task_id()
```

- [ ] **Step 2: Run failing tests**

Run: `uv run pytest tests/daemon/test_org_state.py -v`
Expected: ImportError — `src.daemon.org_state` does not exist yet.

- [ ] **Step 3: Implement `OrgState`**

Create `src/daemon/org_state.py`:

```python
"""Per-org runtime state: DB, queue events, sessions, teams, locks.

One ``OrgState`` per active org under ``<runtime>/orgs/<slug>/``. Constructed
once at daemon startup (via ``DaemonState.from_runtime``) or lazily on
``opc orgs init <slug>``. Each instance is fully self-contained — no
cross-references to other orgs.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from src.config import Settings
from src.daemon.event_bus import EventBus
from src.daemon.sessions import SessionTracker
from src.infrastructure.database import Database
from src.models import BlockKind, TaskStatus
from src.orchestrator.teams import TeamsRegistry


@dataclass
class OrgState:
    slug: str
    root: Path                        # <runtime>/orgs/<slug>
    db: Database
    teams: TeamsRegistry
    settings: Settings
    sessions: SessionTracker = field(default_factory=SessionTracker)
    db_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    kb_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    teams_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    event_bus: EventBus = field(init=False)

    _TERMINAL_STATUS_TO_EVENT = {
        TaskStatus.COMPLETED: "task_complete",
        TaskStatus.FAILED: "task_failed",
    }

    def __post_init__(self) -> None:
        def loader(task_id: str) -> list[dict]:
            history: list[dict] = [
                {"type": "audit", **log}
                for log in self.db.get_audit_logs(task_id)
            ]
            task = self.db.get_task(task_id)
            terminal = self._synthesize_terminal_event(task) if task else None
            if terminal is not None:
                history.append(terminal)
            return history
        self.event_bus = EventBus(history_loader=loader)

    def _synthesize_terminal_event(self, task) -> dict | None:
        if task.status in self._TERMINAL_STATUS_TO_EVENT:
            return {
                "type": self._TERMINAL_STATUS_TO_EVENT[task.status],
                "outcome": task.status.value,
                "synthesized": True,
            }
        if task.status == TaskStatus.BLOCKED and task.block_kind == BlockKind.ESCALATED:
            return {
                "type": "task_blocked",
                "outcome": "escalated",
                "synthesized": True,
            }
        return None

    @classmethod
    def load(cls, *, slug: str, root: Path, settings: Settings) -> "OrgState":
        db = Database(root / "opc.db")
        teams = TeamsRegistry.load(root)
        return cls(
            slug=slug,
            root=root,
            db=db,
            teams=teams,
            settings=settings,
        )

    def close(self) -> None:
        self.db.close()
```

`TeamsRegistry.load` currently takes a `RuntimeDir` and reads `runtime.teams_config_path` (i.e., `<runtime>/org/teams.yaml`). After this task it should accept any path that resembles a runtime root: change the signature to `TeamsRegistry.load(root: Path)` and read `<root>/org/teams.yaml`. Update existing callers in `src/runtime.py` (deleted block — gone) and `src/daemon/state.py` (rewritten in Task 4) accordingly. For now it's referenced only here.

In `src/orchestrator/teams.py`, change:

```python
@classmethod
def load(cls, runtime: RuntimeDir) -> "TeamsRegistry":
    path = runtime.teams_config_path
    ...
```

to:

```python
@classmethod
def load(cls, root: Path) -> "TeamsRegistry":
    """Load from <root>/org/teams.yaml. ``root`` is an org root (or, in
    legacy single-org runtimes, the runtime root)."""
    path = root / "org" / "teams.yaml"
    ...
```

Update the same module's `seed_empty` to take `root: Path` likewise.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/daemon/test_org_state.py -v`
Expected: PASS.

- [ ] **Step 5: Verify `Database.close` exists**

Run: `uv run python -c "from src.infrastructure.database import Database; print(hasattr(Database, 'close'))"`
Expected: `True`. (If it prints `False`, add a `def close(self) -> None: self._conn.close()` method to `Database` in a separate small commit before continuing.)

- [ ] **Step 6: Commit**

```bash
git add src/daemon/org_state.py src/orchestrator/teams.py tests/daemon/test_org_state.py
git commit -m "feat(daemon): add OrgState — per-org DB/teams/sessions/locks

OrgState is the per-org shard that DaemonState will hold N of. Loads
from <runtime>/orgs/<slug>/, opens its own SQLite, builds its own
EventBus and SessionTracker. TeamsRegistry.load now takes a path so
it works against any runtime-shaped root."
```

---

### Task 3: `TaskQueue` carries `(slug, task_id)` tuples

**Files:**
- Modify: `src/daemon/queue.py`
- Modify: `tests/daemon/test_queue.py`

- [ ] **Step 1: Write failing test for tuple round-trip**

Add to `tests/daemon/test_queue.py`:

```python
import asyncio

import pytest

from src.daemon.queue import TaskQueue


def test_enqueue_takes_slug_and_id() -> None:
    q = TaskQueue()
    q.enqueue("alpha", "TASK-001")
    q.enqueue("beta", "TASK-001")
    # Internal state: tuples in the underlying queue
    items = []
    while not q._queue.empty():
        items.append(q._queue.get_nowait())
    assert items == [("alpha", "TASK-001"), ("beta", "TASK-001")]


@pytest.mark.asyncio
async def test_drain_sync_dispatches_per_slug() -> None:
    q = TaskQueue()
    q.enqueue("alpha", "TASK-001")
    q.enqueue("beta", "TASK-002")

    seen: list[tuple[str, str]] = []

    class FakeOrch:
        def run_step(self, slug: str, task_id: str) -> None:
            seen.append((slug, task_id))

    await q.drain_sync(FakeOrch())
    assert sorted(seen) == [("alpha", "TASK-001"), ("beta", "TASK-002")]
```

- [ ] **Step 2: Run failing tests**

Run: `uv run pytest tests/daemon/test_queue.py -v`
Expected: TypeError on `enqueue("alpha", "TASK-001")` — current signature is `enqueue(task_id)`.

- [ ] **Step 3: Update `TaskQueue` to carry tuples**

Replace `src/daemon/queue.py`:

```python
"""Asyncio queue + worker pool for invoking Orchestrator.run_step.

Items are ``(slug, task_id)`` tuples. The worker loop unpacks each item,
looks up ``state.get_org(slug)``, and calls that org's
``Orchestrator.run_step(task_id)`` on a thread.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from src.daemon.state import DaemonState

logger = logging.getLogger("opc.daemon.queue")


class _Dispatcher(Protocol):
    def run_step(self, slug: str, task_id: str) -> None: ...


class TaskQueue:
    """Wrapper around asyncio.Queue + a worker pool."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self._worker_tasks: list[asyncio.Task] = []
        self._stopping = False

    def enqueue(self, slug: str, task_id: str) -> None:
        self._queue.put_nowait((slug, task_id))

    def put_nowait(self, slug: str, task_id: str) -> None:
        self.enqueue(slug, task_id)

    async def _worker_loop(self, dispatcher: _Dispatcher) -> None:
        loop = asyncio.get_running_loop()
        while not self._stopping:
            slug, task_id = await self._queue.get()
            try:
                await loop.run_in_executor(
                    None, dispatcher.run_step, slug, task_id,
                )
            except Exception:
                logger.exception(
                    "run_step %s/%s raised — continuing", slug, task_id,
                )
            finally:
                self._queue.task_done()

    def start_workers(self, dispatcher: _Dispatcher, n: int = 3) -> None:
        for _ in range(n):
            self._worker_tasks.append(
                asyncio.create_task(self._worker_loop(dispatcher))
            )

    def is_running(self) -> bool:
        return any(not t.done() for t in self._worker_tasks)

    async def stop(self, *, timeout: float = 5.0) -> None:
        self._stopping = True
        for t in self._worker_tasks:
            t.cancel()
        await asyncio.gather(*self._worker_tasks, return_exceptions=True)

    async def drain_sync(self, dispatcher: _Dispatcher) -> None:
        loop = asyncio.get_running_loop()
        while not self._queue.empty():
            slug, task_id = self._queue.get_nowait()
            try:
                await loop.run_in_executor(
                    None, dispatcher.run_step, slug, task_id,
                )
            except Exception:
                logger.exception(
                    "run_step %s/%s raised during drain", slug, task_id,
                )
            finally:
                self._queue.task_done()
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/daemon/test_queue.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/queue.py tests/daemon/test_queue.py
git commit -m "refactor(queue): TaskQueue carries (slug, task_id) tuples

Workers now receive both the org slug and the task id so they can
dispatch to the right OrgState.orchestrator. The Dispatcher protocol
formalizes what start_workers expects from its callee."
```

---

## Phase 2: `DaemonState` registry + dispatcher

Goal: rebuild `DaemonState` around `OrgState`s, wire the new dispatcher, and load every org under `<runtime>/orgs/` at startup. After this phase the daemon boots a registry but routes are still on the old single-runtime API and will fail to start until Phase 3 catches up — that's the deliberate sequencing.

### Task 4: `DaemonState` becomes a registry

**Files:**
- Modify: `src/daemon/state.py`
- Modify: `tests/daemon/test_state.py` (likely needs renaming/creation; check existing layout)

- [ ] **Step 1: Write failing test for registry semantics**

Create or extend `tests/daemon/test_state.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.config import Settings
from src.daemon.state import DaemonState
from src.runtime import RuntimeDir


def _seed_org(org_root: Path) -> None:
    org_root.mkdir(parents=True)
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text("teams: {}\n")
    (org_root / "org" / "agents").mkdir()
    (org_root / "workspaces").mkdir()
    (org_root / "kb").mkdir()
    (org_root / "talks").mkdir()


def test_from_runtime_loads_all_orgs(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt")
    _seed_org(rt.orgs_dir / "alpha")
    _seed_org(rt.orgs_dir / "beta")
    _seed_org(rt.orgs_dir / "_pending")  # reserved, must be skipped
    state = DaemonState.from_runtime(rt, Settings())
    assert sorted(state.orgs.keys()) == ["alpha", "beta"]


def test_get_org_unknown_raises(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt")
    _seed_org(rt.orgs_dir / "alpha")
    state = DaemonState.from_runtime(rt, Settings())
    with pytest.raises(KeyError):
        state.get_org("does-not-exist")


@pytest.mark.asyncio
async def test_add_org_idempotent(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt")
    _seed_org(rt.orgs_dir / "alpha")
    state = DaemonState.from_runtime(rt, Settings())
    org_a1 = await state.add_org("alpha")
    org_a2 = await state.add_org("alpha")
    assert org_a1 is org_a2  # same instance, not reloaded
```

- [ ] **Step 2: Run failing tests**

Run: `uv run pytest tests/daemon/test_state.py -v`
Expected: AttributeError — `DaemonState.from_runtime` doesn't return an org-registry yet.

- [ ] **Step 3: Replace `DaemonState`**

Replace `src/daemon/state.py`:

```python
"""Process-wide state holder for the daemon."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from src.config import Settings
from src.daemon.org_state import OrgState
from src.daemon.queue import TaskQueue
from src.runtime import RuntimeDir


@dataclass
class DaemonState:
    runtime: RuntimeDir | None
    settings: Settings
    orgs: dict[str, OrgState] = field(default_factory=dict)
    queue: TaskQueue = field(default_factory=TaskQueue)
    orgs_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @classmethod
    def idle(cls, settings: Settings) -> "DaemonState":
        return cls(runtime=None, settings=settings)

    @classmethod
    def from_runtime(cls, runtime: RuntimeDir, settings: Settings) -> "DaemonState":
        state = cls(runtime=runtime, settings=settings)
        for slug, root in runtime.iter_org_roots():
            state.orgs[slug] = OrgState.load(slug=slug, root=root, settings=settings)
        return state

    @property
    def is_idle(self) -> bool:
        return self.runtime is None

    def get_org(self, slug: str) -> OrgState:
        try:
            return self.orgs[slug]
        except KeyError as exc:
            raise KeyError(slug) from exc

    async def add_org(self, slug: str) -> OrgState:
        """Lazy-load an org's OrgState. Idempotent — returns the existing
        instance if the slug is already loaded."""
        async with self.orgs_lock:
            if slug in self.orgs:
                return self.orgs[slug]
            assert self.runtime is not None
            root = self.runtime.orgs_dir / slug
            org = OrgState.load(slug=slug, root=root, settings=self.settings)
            self.orgs[slug] = org
            return org

    async def remove_org(self, slug: str) -> None:
        async with self.orgs_lock:
            org = self.orgs.pop(slug, None)
            if org is not None:
                org.close()

    async def close_all(self) -> None:
        async with self.orgs_lock:
            for org in self.orgs.values():
                org.close()
            self.orgs.clear()
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/daemon/test_state.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/state.py tests/daemon/test_state.py
git commit -m "refactor(daemon): DaemonState becomes a registry of OrgStates

Holds one OrgState per active org. Discovers all orgs at startup via
RuntimeDir.iter_org_roots; supports lazy add_org for the create-org
endpoint. The old single-runtime fields (db, teams, locks, sessions,
event_bus) are gone — those moved into OrgState."
```

---

### Task 5: `enqueue_task` takes a slug; build a queue dispatcher

**Files:**
- Modify: `src/daemon/runner.py`
- Create: `src/daemon/dispatcher.py`
- Modify: `tests/daemon/test_runner.py`

- [ ] **Step 1: Write failing tests**

Replace `tests/daemon/test_runner.py`'s body with:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from src.config import Settings
from src.daemon.runner import enqueue_task
from src.daemon.state import DaemonState
from src.runtime import RuntimeDir


def _seed_org(org_root: Path) -> None:
    org_root.mkdir(parents=True)
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text("teams: {}\n")
    (org_root / "workspaces").mkdir()
    (org_root / "kb").mkdir()
    (org_root / "talks").mkdir()


def test_enqueue_task_pushes_tuple(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt")
    _seed_org(rt.orgs_dir / "alpha")
    state = DaemonState.from_runtime(rt, Settings())
    enqueue_task(state, "alpha", "TASK-001")
    assert state.queue._queue.get_nowait() == ("alpha", "TASK-001")


def test_enqueue_task_idle_raises(tmp_path: Path) -> None:
    state = DaemonState.idle(Settings())
    with pytest.raises(RuntimeError, match="idle"):
        enqueue_task(state, "alpha", "TASK-001")
```

- [ ] **Step 2: Run failing tests**

Run: `uv run pytest tests/daemon/test_runner.py -v`
Expected: TypeError — `enqueue_task` still has the old single-arg signature.

- [ ] **Step 3: Update `runner.py`**

Replace `src/daemon/runner.py`:

```python
"""Task enqueue entry point for the daemon."""
from __future__ import annotations

from src.daemon.state import DaemonState


def enqueue_task(state: DaemonState, slug: str, task_id: str) -> None:
    if state.is_idle:
        raise RuntimeError("daemon is idle — no active runtime")
    state.queue.enqueue(slug, task_id)
```

- [ ] **Step 4: Add `Dispatcher`**

Create `src/daemon/dispatcher.py`:

```python
"""Worker-side dispatcher: pop (slug, task_id), route to the right Orchestrator."""
from __future__ import annotations

import logging

from src.daemon.state import DaemonState

logger = logging.getLogger("opc.daemon.dispatcher")


class Dispatcher:
    """Resolves ``(slug, task_id)`` to ``OrgState.orchestrator.run_step``.

    Built once per daemon lifetime; not thread-safe except for what
    DaemonState.orgs guarantees (the dict isn't modified concurrently with
    reads under normal operation; ``add_org`` / ``remove_org`` hold the
    orgs_lock).
    """

    def __init__(self, state: DaemonState) -> None:
        self._state = state

    def run_step(self, slug: str, task_id: str) -> None:
        try:
            org = self._state.get_org(slug)
        except KeyError:
            logger.warning(
                "dropping run_step for unknown org %r (task %s) — "
                "org may have been unloaded",
                slug,
                task_id,
            )
            return
        org.orchestrator.run_step(task_id)
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/daemon/test_runner.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/daemon/runner.py src/daemon/dispatcher.py tests/daemon/test_runner.py
git commit -m "feat(daemon): slug-aware enqueue + Dispatcher for worker pool

enqueue_task gains a slug arg and pushes (slug, task_id) tuples onto
the global queue. Dispatcher.run_step is the function the worker pool
calls; it resolves the slug to an OrgState and forwards to that org's
Orchestrator."
```

---

### Task 6: Wire `DaemonState` + `Dispatcher` into the lifespan

**Files:**
- Modify: `src/daemon/app.py`

- [ ] **Step 1: Update `ensure_workers_started`**

Replace `src/daemon/app.py`:

```python
"""FastAPI app factory."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.daemon.dispatcher import Dispatcher
from src.daemon.routes import (
    agents,
    audit,
    health,
    kb,
    orgs,
    runtime,
    talks,
    tasks,
)
from src.daemon.state import DaemonState


def ensure_workers_started(state: DaemonState) -> None:
    """Start the worker pool if a runtime is active and workers aren't running.

    Idempotent. Each org's Orchestrator is built once when the org is loaded
    (see OrgState.load); the Dispatcher routes (slug, task_id) tuples to the
    right one.
    """
    if state.is_idle:
        return
    if state.queue.is_running():
        return
    dispatcher = Dispatcher(state)
    state.queue.start_workers(dispatcher, n=3)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    state: DaemonState = app.state.daemon
    ensure_workers_started(state)
    try:
        yield
    finally:
        await state.queue.stop()
        await state.close_all()


def create_app(state: DaemonState) -> FastAPI:
    app = FastAPI(title="OPC Daemon", version="0.2.0", lifespan=_lifespan)
    app.state.daemon = state
    app.include_router(health.router, prefix="/api/v1")
    app.include_router(runtime.router, prefix="/api/v1")
    app.include_router(orgs.router, prefix="/api/v1")
    app.include_router(tasks.router, prefix="/api/v1")
    app.include_router(agents.router, prefix="/api/v1")
    app.include_router(audit.router, prefix="/api/v1")
    app.include_router(kb.router, prefix="/api/v1")
    app.include_router(talks.router, prefix="/api/v1")
    return app
```

- [ ] **Step 2: Note the broken includes**

`runtime` and `orgs` modules don't exist yet, and the per-org route modules still reference the old single-runtime API. Don't run tests yet — Phase 3 builds them.

- [ ] **Step 3: Stash and verify the file structure compiles isolated**

Run: `uv run python -c "import src.daemon.state; import src.daemon.dispatcher; import src.daemon.queue; print('ok')"`
Expected: `ok`. (The full app.py won't import yet because of missing routes — that's expected; we only want to confirm Phase 1+2 modules are sound.)

- [ ] **Step 4: Commit**

```bash
git add src/daemon/app.py
git commit -m "wire(daemon): lifespan loads orgs, Dispatcher drives workers

Imports for routes/runtime and routes/orgs land in this commit but the
modules don't exist yet — Phase 3 builds them. The full app.py won't
import cleanly until then. This is the deliberate sequencing."
```

---

## Phase 3: Cross-org HTTP routes

Goal: implement `OrgDep`, `/api/v1/orgs`, `/api/v1/runtime` (singular). Per-org routes will move in Phase 4.

### Task 7: `OrgDep` dependency

**Files:**
- Create: `src/daemon/routes/_org_dep.py`
- Create: `tests/daemon/test_org_dep.py`

- [ ] **Step 1: Write failing test**

Create `tests/daemon/test_org_dep.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config import Settings
from src.daemon.routes._org_dep import OrgDep
from src.daemon.state import DaemonState
from src.runtime import RuntimeDir


def _seed_org(org_root: Path) -> None:
    org_root.mkdir(parents=True)
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text("teams: {}\n")


def _make_app(state: DaemonState) -> FastAPI:
    app = FastAPI()
    app.state.daemon = state

    @app.get("/api/v1/orgs/{slug}/echo")
    def echo(slug: str, org: OrgDep) -> dict:
        return {"slug": org.slug}

    return app


def test_org_dep_resolves(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt")
    _seed_org(rt.orgs_dir / "alpha")
    state = DaemonState.from_runtime(rt, Settings())
    client = TestClient(_make_app(state))
    r = client.get("/api/v1/orgs/alpha/echo")
    assert r.status_code == 200
    assert r.json() == {"slug": "alpha"}


def test_org_dep_unknown_404(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt")
    _seed_org(rt.orgs_dir / "alpha")
    state = DaemonState.from_runtime(rt, Settings())
    client = TestClient(_make_app(state))
    r = client.get("/api/v1/orgs/nope/echo")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "unknown_org"
    assert r.json()["detail"]["available"] == ["alpha"]


def test_org_dep_idle_409(tmp_path: Path) -> None:
    state = DaemonState.idle(Settings())
    client = TestClient(_make_app(state))
    r = client.get("/api/v1/orgs/alpha/echo")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "no_active_runtime"
```

- [ ] **Step 2: Run, expect ImportError**

Run: `uv run pytest tests/daemon/test_org_dep.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement**

Create `src/daemon/routes/_org_dep.py`:

```python
"""Shared FastAPI dependency: resolve a path slug to its OrgState."""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from src.daemon.org_state import OrgState
from src.daemon.state import DaemonState


def resolve_org(slug: str, request: Request) -> OrgState:
    state: DaemonState = request.app.state.daemon
    if state.is_idle:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "no_active_runtime"},
        )
    try:
        return state.get_org(slug)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "unknown_org",
                "slug": slug,
                "available": sorted(state.orgs.keys()),
            },
        )


OrgDep = Annotated[OrgState, Depends(resolve_org)]
```

- [ ] **Step 4: Run**

Run: `uv run pytest tests/daemon/test_org_dep.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/_org_dep.py tests/daemon/test_org_dep.py
git commit -m "feat(routes): OrgDep — slug-from-path → OrgState resolver

Used by every per-org route in Phase 4. Returns 404 with the available
slug list on unknown slug, 409 if the daemon is idle."
```

---

### Task 8: `/api/v1/orgs` router (list, init, unload)

**Files:**
- Create: `src/daemon/routes/orgs.py`
- Create: `tests/daemon/test_routes_orgs.py`

- [ ] **Step 1: Write failing test**

Create `tests/daemon/test_routes_orgs.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from src.config import Settings
from src.daemon.app import create_app
from src.daemon.state import DaemonState
from src.runtime import RuntimeDir


def _seed_org(org_root: Path) -> None:
    org_root.mkdir(parents=True)
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text("teams: {}\n")


@pytest.fixture
def auth(monkeypatch, tmp_path):
    home = tmp_path / "opc-home"
    home.mkdir()
    monkeypatch.setenv("OPC_DAEMON_HOME", str(home))
    from src.daemon import paths
    token = paths.ensure_token()
    return {"Authorization": f"Bearer {token}"}


def test_list_orgs_returns_loaded(tmp_path: Path, auth) -> None:
    rt = RuntimeDir.init(tmp_path / "rt")
    _seed_org(rt.orgs_dir / "alpha")
    _seed_org(rt.orgs_dir / "beta")
    state = DaemonState.from_runtime(rt, Settings())
    client = TestClient(create_app(state))
    r = client.get("/api/v1/orgs", headers=auth)
    assert r.status_code == 200
    assert sorted(o["slug"] for o in r.json()["orgs"]) == ["alpha", "beta"]


def test_init_org_creates_skeleton_and_loads(tmp_path: Path, auth) -> None:
    rt = RuntimeDir.init(tmp_path / "rt")
    state = DaemonState.from_runtime(rt, Settings())
    client = TestClient(create_app(state))
    r = client.post("/api/v1/orgs", headers=auth, json={"slug": "alpha"})
    assert r.status_code == 200
    assert (rt.orgs_dir / "alpha" / "org" / "teams.yaml").is_file()
    assert "alpha" in state.orgs


def test_init_org_invalid_slug_400(tmp_path: Path, auth) -> None:
    rt = RuntimeDir.init(tmp_path / "rt")
    state = DaemonState.from_runtime(rt, Settings())
    client = TestClient(create_app(state))
    r = client.post("/api/v1/orgs", headers=auth, json={"slug": "Bad Slug"})
    assert r.status_code == 400


def test_init_org_idempotent_returns_409(tmp_path: Path, auth) -> None:
    rt = RuntimeDir.init(tmp_path / "rt")
    _seed_org(rt.orgs_dir / "alpha")
    state = DaemonState.from_runtime(rt, Settings())
    client = TestClient(create_app(state))
    r = client.post("/api/v1/orgs", headers=auth, json={"slug": "alpha"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "org_exists"
```

- [ ] **Step 2: Run failing tests**

Run: `uv run pytest tests/daemon/test_routes_orgs.py -v`
Expected: ImportError on `routes.orgs`.

- [ ] **Step 3: Implement**

Create `src/daemon/routes/orgs.py`:

```python
"""Cross-org endpoints: list, init, unload."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, field_validator

from src.daemon.auth import require_token
from src.daemon.org_state import OrgState
from src.daemon.state import DaemonState

router = APIRouter(dependencies=[require_token()])

_SLUG_RE = re.compile(r"^[a-z0-9-]{1,40}$")
_RESERVED = frozenset({"_pending", "_archive"})


class InitOrgBody(BaseModel):
    slug: str
    from_example: str | None = None  # path to examples/orgs/<name> tree

    @field_validator("slug")
    @classmethod
    def _validate_slug(cls, v: str) -> str:
        if not _SLUG_RE.match(v) or v in _RESERVED:
            raise ValueError(f"invalid slug: {v!r}")
        return v


def _require_runtime(state: DaemonState) -> None:
    if state.is_idle:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "no_active_runtime"},
        )


def _seed_skeleton(org_root: Path, *, from_example: Path | None) -> None:
    org_root.mkdir(parents=True, exist_ok=False)
    if from_example is not None:
        # Copy examples/orgs/<name>/org/ verbatim into <org_root>/org/.
        src_org = from_example / "org"
        if not src_org.is_dir():
            raise HTTPException(
                status_code=400,
                detail={"code": "example_missing_org_dir", "path": str(src_org)},
            )
        shutil.copytree(src_org, org_root / "org")
    else:
        (org_root / "org").mkdir()
        (org_root / "org" / "agents").mkdir()
        (org_root / "org" / "agents" / "_pending").mkdir()
        (org_root / "org" / "teams.yaml").write_text("teams: {}\n")
    (org_root / "workspaces").mkdir(exist_ok=True)
    (org_root / "kb").mkdir(exist_ok=True)
    (org_root / "talks").mkdir(exist_ok=True)


@router.get("/orgs")
async def list_orgs(request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_runtime(state)
    return {
        "orgs": [
            {"slug": slug, "root": str(org.root)}
            for slug, org in sorted(state.orgs.items())
        ],
    }


@router.post("/orgs")
async def init_org(body: InitOrgBody, request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_runtime(state)
    if body.slug in state.orgs:
        raise HTTPException(
            status_code=409,
            detail={"code": "org_exists", "slug": body.slug},
        )
    org_root = state.runtime.orgs_dir / body.slug
    if org_root.exists():
        raise HTTPException(
            status_code=409,
            detail={"code": "org_dir_exists", "slug": body.slug, "path": str(org_root)},
        )
    from_example = Path(body.from_example).expanduser() if body.from_example else None
    _seed_skeleton(org_root, from_example=from_example)
    org = await state.add_org(body.slug)
    return {"slug": org.slug, "root": str(org.root)}


@router.delete("/orgs/{slug}")
async def unload_org(slug: str, request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_runtime(state)
    if slug not in state.orgs:
        raise HTTPException(
            status_code=404,
            detail={"code": "unknown_org", "slug": slug},
        )
    await state.remove_org(slug)
    return {"slug": slug, "unloaded": True}
```

- [ ] **Step 4: Run**

Run: `uv run pytest tests/daemon/test_routes_orgs.py -v`
Expected: PASS (assuming `routes/runtime.py` import in `app.py` is also satisfied — if not, the next task fixes that and the test will run on the next pass).

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/orgs.py tests/daemon/test_routes_orgs.py
git commit -m "feat(routes): /api/v1/orgs — list, init, unload"
```

---

### Task 9: `/api/v1/runtime` (singular) router

**Files:**
- Create: `src/daemon/routes/runtime.py`
- Create: `tests/daemon/test_routes_runtime.py`

- [ ] **Step 1: Write failing test**

Create `tests/daemon/test_routes_runtime.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.config import Settings
from src.daemon.app import create_app
from src.daemon.state import DaemonState
from src.runtime import RuntimeDir


@pytest.fixture
def auth(monkeypatch, tmp_path):
    home = tmp_path / "opc-home"
    home.mkdir()
    monkeypatch.setenv("OPC_DAEMON_HOME", str(home))
    from src.daemon import paths
    return {"Authorization": f"Bearer {paths.ensure_token()}"}


def test_get_runtime_idle(tmp_path: Path, auth) -> None:
    state = DaemonState.idle(Settings())
    client = TestClient(create_app(state))
    r = client.get("/api/v1/runtime", headers=auth)
    assert r.status_code == 200
    assert r.json() == {"runtime": None}


def test_post_runtime_registers(tmp_path: Path, auth) -> None:
    state = DaemonState.idle(Settings())
    client = TestClient(create_app(state))
    target = tmp_path / "rt"
    r = client.post(
        "/api/v1/runtime", headers=auth,
        json={"path": str(target)},
    )
    assert r.status_code == 200
    assert r.json()["runtime"] == str(target.resolve())
    assert state.runtime is not None
```

- [ ] **Step 2: Implement**

Create `src/daemon/routes/runtime.py`:

```python
"""Singular runtime endpoints: get info, register, switch."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from src.daemon import runtimes as reg
from src.daemon.auth import require_token
from src.daemon.state import DaemonState
from src.runtime import RuntimeDir

router = APIRouter(dependencies=[require_token()])


class RuntimePath(BaseModel):
    path: str


def _swap(state: DaemonState, runtime: RuntimeDir) -> None:
    """Replace the daemon's active runtime atomically."""
    new_state = DaemonState.from_runtime(runtime, state.settings)
    # Move the worker queue and lock instances into the new state so an
    # in-flight worker pool keeps consuming. Note: this swap happens only
    # when the queue is empty (use endpoint enforces it).
    new_state.queue = state.queue
    new_state.orgs_lock = state.orgs_lock
    state.runtime = new_state.runtime
    state.orgs = new_state.orgs


@router.get("/runtime")
async def get_runtime(request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    return {"runtime": str(state.runtime.root) if state.runtime else None}


@router.post("/runtime")
async def register_runtime(body: RuntimePath, request: Request) -> dict:
    from src.daemon.app import ensure_workers_started

    daemon: DaemonState = request.app.state.daemon
    path = Path(body.path).expanduser()
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
    runtime = RuntimeDir.init(path)
    reg.register(path)
    _swap(daemon, runtime)
    ensure_workers_started(daemon)
    return {"runtime": str(path.resolve())}


@router.post("/runtime/use")
async def use_runtime(body: RuntimePath, request: Request) -> dict:
    from src.daemon.app import ensure_workers_started

    daemon: DaemonState = request.app.state.daemon
    path = Path(body.path).expanduser().resolve()
    runtime = RuntimeDir.load(path)

    async with daemon.orgs_lock:
        for org in daemon.orgs.values():
            in_flight = org.db.get_nonterminal_task_ids()
            if in_flight:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "active_tasks_in_flight",
                        "org": org.slug,
                        "task_ids": in_flight,
                    },
                )
        reg.activate(path)
        for org in list(daemon.orgs.values()):
            org.close()
        daemon.orgs.clear()
        _swap(daemon, runtime)
    ensure_workers_started(daemon)
    return {"runtime": str(path)}
```

- [ ] **Step 3: Delete `src/daemon/routes/runtimes.py` (plural)**

Run: `git rm src/daemon/routes/runtimes.py tests/daemon/test_routes_runtimes.py`

- [ ] **Step 4: Run**

Run: `uv run pytest tests/daemon/test_routes_runtime.py tests/daemon/test_routes_orgs.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/runtime.py tests/daemon/test_routes_runtime.py
git rm -f src/daemon/routes/runtimes.py tests/daemon/test_routes_runtimes.py 2>/dev/null || true
git commit -m "feat(routes): /api/v1/runtime singular; remove plural /runtimes"
```

---

## Phase 4: Per-org route prefix migration

Goal: every existing per-org route gains the `/orgs/{slug}` prefix and reads through `OrgDep`. After this phase the daemon's HTTP surface matches the spec; CLI hasn't moved yet (Phase 6 catches up).

For each route file in this phase, the pattern is:

1. Add `slug: str` and `org: OrgDep` to every endpoint signature.
2. Replace `state: DaemonState = request.app.state.daemon` reads with `org: OrgDep`.
3. Replace `state.db` → `org.db`, `state.event_bus` → `org.event_bus`, `state.sessions` → `org.sessions`, `state.db_lock` → `org.db_lock`, `state.kb_lock` → `org.kb_lock`, `state.teams` → `org.teams`, `state.runtime` → `org.root` (where the runtime root was used for workspace paths).
4. The router's path prefix changes from no prefix to `prefix="/orgs/{slug}"` at `include_router` time, OR each route's path becomes `/orgs/{slug}/...` directly. Keep router-level prefix to minimize per-route changes.
5. Update the matching test file: every URL `/api/v1/tasks` becomes `/api/v1/orgs/{slug}/tasks`; fixtures construct `DaemonState.from_runtime` with at least one seeded org.

The substantive logic of each endpoint does not change.

### Task 10: Migrate `routes/tasks.py`

**Files:**
- Modify: `src/daemon/routes/tasks.py`
- Modify: `tests/daemon/test_routes_tasks.py`
- Modify: `src/daemon/app.py:include_router(tasks.router)` — pass `prefix="/orgs/{slug}"` if you take the prefix-at-include path; otherwise edit each route path.

- [ ] **Step 1: Refactor `routes/tasks.py`**

Apply the rule above: replace every `state: DaemonState = request.app.state.daemon` block with the dependency `org: OrgDep`; replace `state.X` → `org.X` for `db`, `event_bus`, `sessions`, `db_lock`. Replace `state.runtime.workspaces_dir` with `org.root / "workspaces"` (used in `_read_artifact`).

For the `cancel_task` endpoint, the pid-tracking logic is unchanged because it reads from `org.sessions` (already per-org). Keep the `_enqueue_parent_if_waiting` import path unchanged.

The `submit_task` route's `enqueue_task` call changes from `enqueue_task(state, task_id)` to `enqueue_task(state, slug, task_id)`. The `state` reference still comes from `request.app.state.daemon` because `enqueue_task` operates on the global queue.

- [ ] **Step 2: Refactor `tests/daemon/test_routes_tasks.py`**

Helper to build a daemon with one seeded org:

```python
def _make_daemon(tmp_path: Path) -> tuple[FastAPI, DaemonState]:
    rt = RuntimeDir.init(tmp_path / "rt")
    org_root = rt.orgs_dir / "alpha"
    _seed_org(org_root)
    state = DaemonState.from_runtime(rt, Settings())
    return create_app(state), state
```

Every URL changes from `/api/v1/tasks/...` to `/api/v1/orgs/alpha/tasks/...`.

- [ ] **Step 3: Run**

Run: `uv run pytest tests/daemon/test_routes_tasks.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/daemon/routes/tasks.py tests/daemon/test_routes_tasks.py
git commit -m "refactor(routes): tasks under /orgs/{slug}/tasks via OrgDep"
```

---

### Task 11: Migrate `routes/agents.py`

Same pattern. Note that `agents.py` includes `_append_to_learnings_file` which is imported from `routes/talks.py` later; its filesystem path changes from `state.runtime.workspaces_dir / agent / "learnings.md"` to `org.root / "workspaces" / agent / "learnings.md"`.

- [ ] **Step 1: Refactor `src/daemon/routes/agents.py`** (apply the standard rule)

- [ ] **Step 2: Refactor `tests/daemon/test_routes_agents.py`** (URL prefix + fixture)

- [ ] **Step 3: Run + commit**

Run: `uv run pytest tests/daemon/test_routes_agents.py -v`
Expected: PASS.

```bash
git add src/daemon/routes/agents.py tests/daemon/test_routes_agents.py
git commit -m "refactor(routes): agents under /orgs/{slug}/agents via OrgDep"
```

---

### Task 12: Migrate `routes/audit.py`

- [ ] **Step 1: Refactor** — same pattern.

- [ ] **Step 2: Test refactor** — `tests/daemon/test_routes_audit.py`.

- [ ] **Step 3: Run + commit**

Run: `uv run pytest tests/daemon/test_routes_audit.py -v`
Expected: PASS.

```bash
git add src/daemon/routes/audit.py tests/daemon/test_routes_audit.py
git commit -m "refactor(routes): audit under /orgs/{slug}/audit"
```

---

### Task 13: Migrate `routes/kb.py`

The KB store itself is per-runtime today (constructed against `state.runtime.root / "kb"`) — switch to `org.root / "kb"`. The `kb_lock` moves from `state.kb_lock` to `org.kb_lock`.

- [ ] **Step 1: Refactor**

- [ ] **Step 2: Test refactor** — `tests/daemon/test_routes_kb.py` and `tests/daemon/test_kb_delete_team_managers.py`.

- [ ] **Step 3: Run + commit**

Run: `uv run pytest tests/daemon/test_routes_kb.py tests/daemon/test_kb_delete_team_managers.py -v`
Expected: PASS.

```bash
git add src/daemon/routes/kb.py tests/daemon/test_routes_kb.py tests/daemon/test_kb_delete_team_managers.py
git commit -m "refactor(routes): kb under /orgs/{slug}/kb"
```

---

### Task 14: Migrate `routes/talks.py`

Talks store is `TalkStore(org.root / "talks")`. `_store(state)` becomes `_store(org)` and reads from `org.root`.

- [ ] **Step 1: Refactor**

- [ ] **Step 2: Test refactor** — `tests/daemon/test_talks_routes.py`, `tests/daemon/test_talks_dispatch.py`.

- [ ] **Step 3: Run + commit**

Run: `uv run pytest tests/daemon/test_talks_routes.py tests/daemon/test_talks_dispatch.py -v`
Expected: PASS.

```bash
git add src/daemon/routes/talks.py tests/daemon/test_talks_routes.py tests/daemon/test_talks_dispatch.py
git commit -m "refactor(routes): talks under /orgs/{slug}/talks"
```

---

### Task 15: Verify the daemon boots end-to-end

- [ ] **Step 1: Full unit-test run**

Run: `uv run pytest tests/ -v 2>&1 | tail -30`
Expected: PASS (any remaining failures are CLI-related and fixed in Phase 6).

If non-CLI test files still fail, address them now. Common stragglers:
- `tests/test_orchestrator.py` — Orchestrator constructed with the runtime root; switch fixture to org root.
- `tests/test_run_step.py`, `tests/test_capabilities.py` — same.
- `tests/test_workspace_adapters.py` — adapter ctors now take `slug`; see Phase 5 Task 17 for the change.

If those tests fail because they were not yet updated, leave them and proceed — they're handled in Phase 5.

- [ ] **Step 2: Smoke-test the daemon manually**

Run:
```bash
scripts/daemon.sh stop 2>/dev/null
OPC_DAEMON_HOME=/tmp/opc-test-home scripts/daemon.sh start
sleep 1
TOKEN=$(cat /tmp/opc-test-home/daemon.token)
PORT=$(cat /tmp/opc-test-home/daemon.port)
curl -sS -H "Authorization: Bearer $TOKEN" http://127.0.0.1:$PORT/api/v1/health
curl -sS -H "Authorization: Bearer $TOKEN" http://127.0.0.1:$PORT/api/v1/runtime
scripts/daemon.sh stop
```
Expected: `/health` returns OK; `/runtime` returns `{"runtime": null}`.

- [ ] **Step 3: Commit only if any test fixtures changed in Step 1**

```bash
git add tests/
git commit -m "test: update fixtures for OrgState-rooted call surfaces"
```

---

## Phase 5: Orchestrator + Workspace adapters

Goal: orchestrator and workspace adapters work against an org root, not a runtime root. Skill files copied into agent workspaces have `{ORG_SLUG}` substituted with the literal slug.

### Task 16: Orchestrator wiring to org root

**Files:**
- Modify: `src/orchestrator/orchestrator.py` (`__init__` signature + property name)
- Modify: `src/orchestrator/run_step.py` (`self._runtime` references — keep symbol name; just feed it the org root)
- Modify: callers (`src/daemon/state.py` builds Orchestrator inside `OrgState.load`)
- Modify: tests as needed

- [ ] **Step 1: Update `Orchestrator.__init__`**

Keep the parameter name `runtime: RuntimeDir`. Replace its body usage of `runtime` with the same path semantics as before (workspaces_dir → `runtime.root / "workspaces"`, etc.).

In `src/runtime.py` (the new container), there is no `workspaces_dir` property — workspaces are per-org, under `<org-root>/workspaces`. Add the convenience property to a small helper class so the Orchestrator's `_runtime` shorthand keeps working:

In `src/orchestrator/_paths.py` (new):

```python
"""Per-org root view used by the orchestrator + workspace adapters."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OrgPaths:
    root: Path

    @property
    def workspaces_dir(self) -> Path:
        return self.root / "workspaces"

    @property
    def org_dir(self) -> Path:
        return self.root / "org"

    @property
    def agents_dir(self) -> Path:
        return self.org_dir / "agents"

    @property
    def pending_agents_dir(self) -> Path:
        return self.agents_dir / "_pending"

    @property
    def teams_config_path(self) -> Path:
        return self.org_dir / "teams.yaml"

    @property
    def org_config_path(self) -> Path:
        return self.org_dir / "config.yaml"

    @property
    def db_path(self) -> Path:
        return self.root / "opc.db"
```

Update `Orchestrator.__init__` to take `org_paths: OrgPaths` (rename the parameter from `runtime` to `org_paths`; replace internal `self._runtime` with `self._paths`). Every call to `self._runtime.workspaces_dir`, `.org_dir`, etc. continues to work because `OrgPaths` exposes the same names.

Add a constructor in `OrgState.load` that builds the Orchestrator:

```python
from src.orchestrator._paths import OrgPaths
from src.orchestrator.orchestrator import Orchestrator
...
@classmethod
def load(cls, *, slug, root, settings):
    db = Database(root / "opc.db")
    teams = TeamsRegistry.load(root)
    org_paths = OrgPaths(root=root)
    orch = Orchestrator(
        db=db, settings=settings, org_paths=org_paths, teams=teams,
    )
    return cls(slug=slug, root=root, db=db, teams=teams,
               settings=settings, orchestrator=orch)
```

(Add `orchestrator: Orchestrator` field to the `OrgState` dataclass; default `None` for tests that build OrgState without it, or make it required and update tests accordingly.)

- [ ] **Step 2: Touch every `prompt_loader`/`workspace_adapter`/`context_builder` site**

These currently take `RuntimeDir`. Switch to `OrgPaths` (or `Path` directly) in the same way — the property names match.

- [ ] **Step 3: Run**

Run: `uv run pytest tests/test_orchestrator.py tests/test_run_step.py tests/test_capabilities.py tests/test_context_builder.py -v`
Expected: PASS (after fixture updates).

- [ ] **Step 4: Commit**

```bash
git add src/orchestrator/_paths.py src/orchestrator/orchestrator.py src/orchestrator/run_step.py src/orchestrator/workspace_adapters.py src/orchestrator/context_builder.py src/orchestrator/prompt_loader.py src/daemon/org_state.py tests/
git commit -m "refactor(orchestrator): use OrgPaths instead of RuntimeDir

OrgPaths exposes the same property names (workspaces_dir, org_dir,
agents_dir, etc.) but is rooted at <runtime>/orgs/<slug>/ instead of
the multi-org container root. The Orchestrator now binds to an org,
not the container."
```

---

### Task 17: Skill `{ORG_SLUG}` substitution

**Files:**
- Modify: `src/orchestrator/workspace_adapters.py:_copy_skills` (Claude + Codex)
- Modify: `protocol/skills/start-task/SKILL.md` (and others) — add `{ORG_SLUG}` placeholder where appropriate
- Modify: `tests/test_workspace_adapters.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_workspace_adapters.py`:

```python
def test_copy_skills_substitutes_org_slug(tmp_path: Path, monkeypatch) -> None:
    # Stand up a fake protocol/skills tree
    proto = tmp_path / "protocol" / "skills" / "start-task"
    proto.mkdir(parents=True)
    (proto / "SKILL.md").write_text(
        "Run: opc report-completion --org {ORG_SLUG} --task-id ...\n"
    )
    monkeypatch.setattr("src.orchestrator.workspace_adapters._SKILLS_SRC", tmp_path / "protocol" / "skills")

    workspace = tmp_path / "ws"
    workspace.mkdir()
    adapter = ClaudeWorkspaceAdapter(slug="hk-tourism", settings=Settings())
    adapter._copy_skills(workspace)

    out = (workspace / ".claude" / "skills" / "start-task" / "SKILL.md").read_text()
    assert "{ORG_SLUG}" not in out
    assert "--org hk-tourism" in out
```

- [ ] **Step 2: Run failing test**

Run: `uv run pytest tests/test_workspace_adapters.py::test_copy_skills_substitutes_org_slug -v`
Expected: FAIL — `_copy_skills` does verbatim copy.

- [ ] **Step 3: Implement substitution**

In `src/orchestrator/workspace_adapters.py`, change both adapters' constructors to accept `slug: str` and store it. In `_copy_skills`, replace `shutil.copy2(src, dst)` with a read+substitute+write:

```python
def _copy_skill_file(self, src: Path, dst: Path) -> None:
    text = src.read_text()
    text = text.replace("{ORG_SLUG}", self._slug)
    dst.write_text(text)
```

Walk the skill tree and call `_copy_skill_file` for `.md` files; preserve `shutil.copy2` for non-text assets (none today, but defensive). Make sure `dst.parent.mkdir(parents=True, exist_ok=True)` is done first.

Pass `slug` through every adapter construction site:
- `OrgState.load` → constructs `ContextBuilder` (or whatever calls the adapter) — pass `slug=self.slug`
- Tests that build adapters directly — pass `slug="test-org"` or similar

Add `_SKILLS_SRC = Path(__file__).resolve().parents[2] / "protocol" / "skills"` (or whatever the existing constant is — preserve the name) so the test's `monkeypatch.setattr` works.

- [ ] **Step 4: Run**

Run: `uv run pytest tests/test_workspace_adapters.py -v`
Expected: PASS.

- [ ] **Step 5: Update protocol skill files**

In each of `protocol/skills/start-task/SKILL.md`, `protocol/skills/manage-repo/SKILL.md`, `protocol/skills/manage-agent/SKILL.md`, `protocol/skills/talk/SKILL.md`, replace every example `opc <subcommand> --task-id ...` line with `opc <subcommand> --org {ORG_SLUG} --task-id ...`. Same for the example payloads referenced in those skills (e.g. JSON snippets for `--from-file`).

Run: `grep -nR "opc " protocol/skills/`
Add `--org {ORG_SLUG}` to every match line that's an example `opc` invocation. Be conservative — pure prose mentions of `opc` (without an actual command-line example) don't need the placeholder.

- [ ] **Step 6: Commit**

```bash
git add src/orchestrator/workspace_adapters.py tests/test_workspace_adapters.py protocol/skills/
git commit -m "feat(workspace): bake ORG_SLUG into copied skill files

Adapters now substitute the org slug at workspace-bootstrap time so
agents' opc callbacks always carry --org. Skills source uses {ORG_SLUG}
placeholder; init-agent re-runs the copy on every invocation."
```

---

## Phase 6: CLI surface

Goal: every CLI command works with the new `--org` model.

### Task 18: `--org` resolution helper

**Files:**
- Modify: `src/cli.py` (top of file)
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_cli.py`:

```python
def test_resolve_org_explicit_flag_wins(monkeypatch) -> None:
    monkeypatch.setenv("OPC_ORG_SLUG", "from-env")
    available = ["alpha", "beta"]
    slug = resolve_org_slug(args_org="from-flag", available=available)
    assert slug == "from-flag"


def test_resolve_org_env_var(monkeypatch) -> None:
    monkeypatch.setenv("OPC_ORG_SLUG", "from-env")
    slug = resolve_org_slug(args_org=None, available=["alpha", "from-env"])
    assert slug == "from-env"


def test_resolve_org_auto_infer_single(monkeypatch) -> None:
    monkeypatch.delenv("OPC_ORG_SLUG", raising=False)
    slug = resolve_org_slug(args_org=None, available=["alpha"])
    assert slug == "alpha"


def test_resolve_org_zero_orgs_errors(monkeypatch, capsys) -> None:
    monkeypatch.delenv("OPC_ORG_SLUG", raising=False)
    with pytest.raises(SystemExit):
        resolve_org_slug(args_org=None, available=[])
    err = capsys.readouterr().err
    assert "no orgs registered" in err


def test_resolve_org_multi_errors(monkeypatch, capsys) -> None:
    monkeypatch.delenv("OPC_ORG_SLUG", raising=False)
    with pytest.raises(SystemExit):
        resolve_org_slug(args_org=None, available=["alpha", "beta"])
    err = capsys.readouterr().err
    assert "alpha" in err
    assert "beta" in err
```

- [ ] **Step 2: Run failing tests**

Run: `uv run pytest tests/test_cli.py -k resolve_org -v`
Expected: ImportError.

- [ ] **Step 3: Implement helper**

Add to `src/cli.py` near the top:

```python
import os
import sys


def resolve_org_slug(*, args_org: str | None, available: list[str]) -> str:
    """Resolve the per-command --org per the spec §7.4 chain."""
    if args_org:
        return args_org
    env = os.environ.get("OPC_ORG_SLUG")
    if env:
        return env
    if len(available) == 1:
        return available[0]
    if not available:
        print(
            "error: no orgs registered yet\n"
            "create one with: opc orgs init <slug> [--from <example-path>]",
            file=sys.stderr,
        )
        sys.exit(1)
    print(
        "error: --org <slug> is required\navailable orgs:",
        file=sys.stderr,
    )
    for slug in sorted(available):
        print(f"  {slug}", file=sys.stderr)
    sys.exit(1)


def _fetch_available_orgs(client) -> list[str]:
    r = client.get("/api/v1/orgs")
    if r.status_code != 200:
        return []
    return [o["slug"] for o in r.json().get("orgs", [])]
```

Add `from src.cli import resolve_org_slug` (or equivalent) to test imports.

- [ ] **Step 4: Run**

Run: `uv run pytest tests/test_cli.py -k resolve_org -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cli.py tests/test_cli.py
git commit -m "feat(cli): resolve_org_slug — flag > env > auto-infer > error"
```

---

### Task 19: Container-level CLI commands

**Files:**
- Modify: `src/cli.py` — replace `cmd_init` (used to take `--slug`), add `cmd_runtime`, update `cmd_use`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Refactor `cmd_init` (container creation)**

```python
def cmd_init(args: argparse.Namespace) -> None:
    client = OpcClient.from_env()
    r = client.post(
        "/api/v1/runtime",
        json={"path": str(Path(args.path).expanduser())},
    )
    if r.status_code != 200:
        print(f"Error ({r.status_code}): {r.text}")
        sys.exit(1)
    print(f"runtime: {r.json()['runtime']}")
```

`opc init <path>` no longer takes `--slug`. Update the argparse subparser definition accordingly.

- [ ] **Step 2: Add `cmd_runtime` (info)**

```python
def cmd_runtime(args: argparse.Namespace) -> None:
    client = OpcClient.from_env()
    r = client.get("/api/v1/runtime")
    body = r.json()
    if body["runtime"] is None:
        print("(no active runtime)")
    else:
        print(f"runtime: {body['runtime']}")
```

- [ ] **Step 3: Update `cmd_use`**

```python
def cmd_use(args: argparse.Namespace) -> None:
    client = OpcClient.from_env()
    r = client.post(
        "/api/v1/runtime/use",
        json={"path": str(Path(args.path).expanduser())},
    )
    if r.status_code == 409:
        print(f"Cannot switch runtime: {r.json()['detail']}")
        sys.exit(1)
    if r.status_code != 200:
        print(f"Error ({r.status_code}): {r.text}")
        sys.exit(1)
    print(f"runtime: {r.json()['runtime']}")
```

- [ ] **Step 4: Wire the new subparsers**

In `main()`:

```python
init = sub.add_parser("init", help="create + register a multi-org runtime container")
init.add_argument("path")
init.set_defaults(func=cmd_init)

runtime = sub.add_parser("runtime", help="show the active runtime")
runtime.set_defaults(func=cmd_runtime)

use = sub.add_parser("use", help="switch the active runtime container")
use.add_argument("path")
use.set_defaults(func=cmd_use)
```

- [ ] **Step 5: Update tests**

Test the end-to-end CLI flow against a TestClient:

```python
def test_cli_init_creates_container(tmp_path: Path, ...):
    # Boot the daemon idle, run `opc init <path>`, assert /runtime returns <path>.
    ...
```

- [ ] **Step 6: Run + commit**

Run: `uv run pytest tests/test_cli.py -k "init or runtime or use" -v`
Expected: PASS.

```bash
git add src/cli.py tests/test_cli.py
git commit -m "feat(cli): opc init/runtime/use for the multi-org container"
```

---

### Task 20: `opc orgs` family

**Files:**
- Modify: `src/cli.py` — `cmd_orgs`, `cmd_orgs_init`, `cmd_orgs_unload`

- [ ] **Step 1: Implement**

```python
def cmd_orgs(args: argparse.Namespace) -> None:
    client = OpcClient.from_env()
    r = client.get("/api/v1/orgs")
    if not _ok(r):
        return
    for org in r.json()["orgs"]:
        print(f"  {org['slug']:30s}  {org['root']}")


def cmd_orgs_init(args: argparse.Namespace) -> None:
    client = OpcClient.from_env()
    payload = {"slug": args.slug}
    if args.from_path:
        payload["from_example"] = args.from_path
    r = client.post("/api/v1/orgs", json=payload)
    if not _ok(r):
        return
    print(f"created org: {r.json()['slug']}")


def cmd_orgs_unload(args: argparse.Namespace) -> None:
    client = OpcClient.from_env()
    r = client.request(
        "DELETE", f"/api/v1/orgs/{args.slug}",
    )
    if not _ok(r):
        return
    print(f"unloaded org: {r.json()['slug']}")
```

Argparse:

```python
orgs = sub.add_parser("orgs", help="manage orgs in the active runtime")
orgs_sub = orgs.add_subparsers(dest="orgs_cmd", required=True)

orgs_list = orgs_sub.add_parser("list", help="list orgs")
orgs_list.set_defaults(func=cmd_orgs)

orgs_init = orgs_sub.add_parser("init", help="create a new org")
orgs_init.add_argument("slug")
orgs_init.add_argument("--from", dest="from_path", default=None,
                       help="path to an examples/orgs/<name> tree to seed from")
orgs_init.set_defaults(func=cmd_orgs_init)

orgs_unload = orgs_sub.add_parser("unload", help="drop an org's state from the daemon")
orgs_unload.add_argument("slug")
orgs_unload.set_defaults(func=cmd_orgs_unload)
```

When `opc orgs` is invoked with no subcommand, fall through to `list` semantics for ergonomics — easiest is a parser-level `set_defaults(orgs_cmd="list", func=cmd_orgs)` and let `orgs_sub.required=False`.

- [ ] **Step 2: Add `client.request()` to `OpcClient`**

`src/client/client.py`:

```python
def request(self, method: str, path: str, **kwargs) -> httpx.Response:
    return self._client.request(method, path, **kwargs)
```

- [ ] **Step 3: Tests**

Add tests for each new command.

- [ ] **Step 4: Run + commit**

Run: `uv run pytest tests/test_cli.py -k orgs -v`
Expected: PASS.

```bash
git add src/cli.py src/client/client.py tests/test_cli.py
git commit -m "feat(cli): opc orgs (list/init/unload)"
```

---

### Task 21: Update every per-org CLI command

**Files:**
- Modify: `src/cli.py` — every existing per-org command
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Inventory the commands needing `--org`**

From `src/cli.py`, every command that currently calls `/api/v1/{tasks,agents,audit,kb,talks}/...` needs:
1. `--org <slug>` argument on its argparse subparser.
2. A `slug = resolve_org_slug(args_org=args.org, available=_fetch_available_orgs(client))` line at the top of the function.
3. URL substitution: `/api/v1/tasks` → `/api/v1/orgs/{slug}/tasks` etc.

Affected commands (consult current `src/cli.py`): `cmd_run`, `cmd_tasks`, `cmd_tail`, `cmd_details`, `cmd_audit`, `cmd_agents`, `cmd_init_agent`, `cmd_enrollments`, `cmd_approve_agent`, `cmd_reject_agent`, `cmd_resolve_escalation`, `cmd_revisit`, `cmd_recall`, every `cmd_kb_*`, every `cmd_talk_*`, `cmd_backfill_enrollments`.

- [ ] **Step 2: Mechanical refactor**

For each command, edit:

```python
def cmd_run(args: argparse.Namespace) -> None:
    client = OpcClient.from_env()
    available = _fetch_available_orgs(client)
    slug = resolve_org_slug(args_org=args.org, available=available)
    payload = {"brief": args.brief}
    if args.team:
        payload["team"] = args.team
    r = client.post(f"/api/v1/orgs/{slug}/tasks", json=payload)
    ...
```

Add `--org <slug>` to each argparse subparser definition (default `None`).

- [ ] **Step 3: Tests**

Add a test for one representative command (e.g., `cmd_run`) that asserts:
- explicit `--org` is honored,
- env var is honored when no flag,
- single-org auto-infer works,
- multi-org with no flag and no env errors with the slug list.

- [ ] **Step 4: Run + commit**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS.

```bash
git add src/cli.py tests/test_cli.py
git commit -m "feat(cli): --org on every per-org command (flag/env/auto-infer)"
```

---

### Task 22: Agent-callback CLI commands

These are the commands invoked by skills inside agent workspaces. They MUST take `--org <slug>` because the agent's prompt has it baked in literally — no auto-infer fallback (that's a security boundary).

**Files:**
- Modify: `src/cli.py` — `cmd_report_completion`, `cmd_learning`, `cmd_manage_repo`, `cmd_manage_agent`, `cmd_kb_*` (write side), `cmd_dispatch`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Make `--org` required and remove resolve fallback for callbacks**

For each agent-callback command:

```python
def cmd_report_completion(args: argparse.Namespace) -> None:
    if not args.org:
        print("error: --org <slug> is required for agent callbacks", file=sys.stderr)
        sys.exit(1)
    client = OpcClient.from_env()
    r = client.post(
        f"/api/v1/orgs/{args.org}/tasks/{args.task_id}/completion",
        json={...},
    )
    ...
```

Argparse: `--org <slug>` is `required=True` for these commands.

- [ ] **Step 2: Update test fakes**

Any `tests/integration/conftest.py` fixture that wraps a fake `opc` invocation now passes `--org <slug>`. Update `tests/integration/fake_claude.sh` and `fake_codex.sh` to forward `--org` if they synthesize an `opc` call.

- [ ] **Step 3: Run + commit**

Run: `uv run pytest tests/test_cli.py -k "report or learning or manage" -v`
Expected: PASS.

```bash
git add src/cli.py tests/
git commit -m "feat(cli): agent callbacks require explicit --org

No auto-infer for callbacks — the slug is baked into the agent's
skill files literally. A missing --org is a programming error, not a
user typo."
```

---

## Phase 7: Migration

Goal: convert a v1 single-org runtime into a v2 multi-org container with one org subfolder. Hard cut, founder-run, TTY-gated.

### Task 23: `opc migrate-to-multi-org` script

**Files:**
- Create: `src/daemon/migration_multi_org.py`
- Create: `tests/test_migration_multi_org.py`
- Modify: `src/cli.py` — wire `cmd_migrate_to_multi_org`

- [ ] **Step 1: Write failing tests**

Create `tests/test_migration_multi_org.py`:

```python
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import yaml

from src.daemon.migration_multi_org import migrate_to_multi_org


def _make_v1_runtime(root: Path, slug: str = "hk-tourism") -> None:
    root.mkdir(parents=True)
    (root / "opc.yaml").write_text(yaml.safe_dump({
        "slug": slug,
        "schema_version": 1,
        "created_at": "2026-04-01T00:00:00Z",
    }, sort_keys=False))
    (root / "org").mkdir()
    (root / "org" / "teams.yaml").write_text("teams: {}\n")
    (root / "org" / "agents").mkdir()
    (root / "workspaces").mkdir()
    (root / "kb").mkdir()
    (root / "talks").mkdir()
    # Minimal DB with no in-flight tasks.
    conn = sqlite3.connect(root / "opc.db")
    conn.executescript("""
        CREATE TABLE tasks (id TEXT PRIMARY KEY, status TEXT);
        CREATE TABLE talks (id TEXT PRIMARY KEY, status TEXT);
    """)
    conn.commit()
    conn.close()


def test_migrate_dry_run_does_not_mutate(tmp_path: Path) -> None:
    rt = tmp_path / "rt"
    _make_v1_runtime(rt)
    report = migrate_to_multi_org(rt, apply=False, i_have_a_backup=True)
    assert (rt / "opc.yaml").exists()
    data = yaml.safe_load((rt / "opc.yaml").read_text())
    assert data["schema_version"] == 1  # unchanged
    assert "would_move" in report


def test_migrate_apply_moves_subfolders(tmp_path: Path) -> None:
    rt = tmp_path / "rt"
    _make_v1_runtime(rt, slug="hk")
    migrate_to_multi_org(rt, apply=True, i_have_a_backup=True)
    assert (rt / "orgs" / "hk" / "org" / "teams.yaml").is_file()
    assert (rt / "orgs" / "hk" / "workspaces").is_dir()
    assert (rt / "orgs" / "hk" / "opc.db").is_file()
    assert not (rt / "org").exists()
    assert not (rt / "opc.db").exists()
    data = yaml.safe_load((rt / "opc.yaml").read_text())
    assert data["schema_version"] == 2
    assert data["type"] == "multi-org-runtime"
    assert "slug" not in data


def test_migrate_idempotent(tmp_path: Path) -> None:
    rt = tmp_path / "rt"
    _make_v1_runtime(rt, slug="hk")
    migrate_to_multi_org(rt, apply=True, i_have_a_backup=True)
    # Second run is a no-op
    report = migrate_to_multi_org(rt, apply=True, i_have_a_backup=True)
    assert report["already_migrated"] is True


def test_migrate_refuses_without_backup_ack(tmp_path: Path) -> None:
    rt = tmp_path / "rt"
    _make_v1_runtime(rt)
    with pytest.raises(RuntimeError, match="i-have-a-backup"):
        migrate_to_multi_org(rt, apply=True, i_have_a_backup=False)


def test_migrate_refuses_with_active_tasks(tmp_path: Path) -> None:
    rt = tmp_path / "rt"
    _make_v1_runtime(rt)
    conn = sqlite3.connect(rt / "opc.db")
    conn.execute("INSERT INTO tasks(id, status) VALUES('TASK-001', 'in_progress')")
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="cannot_migrate_with_active_tasks"):
        migrate_to_multi_org(rt, apply=True, i_have_a_backup=True)
```

- [ ] **Step 2: Run failing tests**

Run: `uv run pytest tests/test_migration_multi_org.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement the migration script**

Create `src/daemon/migration_multi_org.py`:

```python
"""One-shot migration from schema_version 1 (single-org runtime) to v2
(multi-org container with one org subfolder)."""
from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import yaml

_TERMINAL_TASK_STATUSES = {"completed", "failed"}
_OPEN_TALK_STATUS = "open"


def _read_marker(rt: Path) -> dict:
    marker = rt / "opc.yaml"
    if not marker.exists():
        raise RuntimeError(f"{marker} does not exist — not a runtime directory")
    return yaml.safe_load(marker.read_text()) or {}


def _list_active_tasks(db_path: Path) -> list[str]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute("SELECT id, status FROM tasks")
        return [
            row[0] for row in cursor
            if row[1] not in _TERMINAL_TASK_STATUSES
            and row[1] != "blocked"  # blocked-delegated is in-flight
        ]
    finally:
        conn.close()


def _list_open_talks(db_path: Path) -> list[str]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            "SELECT id FROM talks WHERE status = ?", (_OPEN_TALK_STATUS,)
        )
        return [row[0] for row in cursor]
    finally:
        conn.close()


def migrate_to_multi_org(
    runtime_path: Path,
    *,
    apply: bool,
    i_have_a_backup: bool,
) -> dict:
    """Convert a v1 runtime into a v2 multi-org container in place.

    Returns a dict describing what was (or would be) done.
    """
    rt = runtime_path.resolve()
    if not i_have_a_backup:
        raise RuntimeError(
            "refusing to migrate without --i-have-a-backup. "
            "back up the runtime folder first."
        )

    marker = _read_marker(rt)
    version = marker.get("schema_version")
    if version == 2:
        return {"already_migrated": True, "runtime": str(rt)}

    if version != 1:
        raise RuntimeError(
            f"unsupported schema_version: {version!r}. "
            f"this script only migrates v1 → v2."
        )

    slug = marker.get("slug")
    if not slug:
        raise RuntimeError(f"v1 marker at {rt}/opc.yaml is missing 'slug'")

    db_path = rt / "opc.db"
    active = _list_active_tasks(db_path)
    if active:
        raise RuntimeError(
            f"cannot_migrate_with_active_tasks: {active}"
        )
    open_talks = _list_open_talks(db_path)
    if open_talks:
        raise RuntimeError(
            f"cannot_migrate_with_open_talks: {open_talks}"
        )

    new_org_root = rt / "orgs" / slug
    plan = {
        "runtime": str(rt),
        "slug": slug,
        "would_move": [
            (str(rt / "org"), str(new_org_root / "org")),
            (str(rt / "workspaces"), str(new_org_root / "workspaces")),
            (str(rt / "kb"), str(new_org_root / "kb")),
            (str(rt / "talks"), str(new_org_root / "talks")),
            (str(rt / "opc.db"), str(new_org_root / "opc.db")),
        ],
        "would_rewrite_marker": True,
    }

    if not apply:
        return plan

    # Apply phase
    new_org_root.mkdir(parents=True, exist_ok=False)
    for src_name in ("org", "workspaces", "kb", "talks"):
        src = rt / src_name
        if src.exists():
            shutil.move(str(src), str(new_org_root / src_name))
    if db_path.exists():
        shutil.move(str(db_path), str(new_org_root / "opc.db"))

    # Rewrite top-level marker (preserve created_at)
    new_marker = {
        "schema_version": 2,
        "type": "multi-org-runtime",
        "created_at": marker.get(
            "created_at",
            datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        ),
    }
    (rt / "opc.yaml").write_text(yaml.safe_dump(new_marker, sort_keys=False))

    plan["already_migrated"] = False
    return plan
```

- [ ] **Step 4: Wire CLI**

In `src/cli.py`:

```python
def cmd_migrate_to_multi_org(args: argparse.Namespace) -> None:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("refusing to migrate without an attached terminal", file=sys.stderr)
        sys.exit(1)
    from src.daemon.migration_multi_org import migrate_to_multi_org

    rt = Path(args.path).expanduser().resolve()
    print(f"about to migrate {rt} from schema v1 → v2")
    print("this is a hard cut. there is no rollback path.")
    if not args.apply:
        print("(dry-run; pass --apply to execute)")
    confirm = input("Continue? [y/N] ").strip().lower()
    if confirm != "y":
        print("aborted")
        sys.exit(1)

    try:
        report = migrate_to_multi_org(
            rt, apply=args.apply, i_have_a_backup=args.i_have_a_backup,
        )
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    if report.get("already_migrated"):
        print(f"{rt} is already at schema v2 — nothing to do")
        return

    if not args.apply:
        print("would move:")
        for src, dst in report["would_move"]:
            print(f"  {src} → {dst}")
        print("\nrun with --apply to execute")
        return

    print(f"migrated. new layout:")
    print(f"  {rt}/orgs/{report['slug']}/")
    print(f"\nnext step:")
    print(f"  uv run opc init-agent --org {report['slug']}")
```

Argparse:

```python
mig = sub.add_parser(
    "migrate-to-multi-org",
    help="convert a v1 single-org runtime into a v2 multi-org container",
)
mig.add_argument("path")
mig.add_argument(
    "--i-have-a-backup",
    action="store_true",
    help="acknowledgment that you have backed up the runtime folder",
)
mig.add_argument("--apply", action="store_true", help="actually execute (default: dry-run)")
mig.set_defaults(func=cmd_migrate_to_multi_org)
```

- [ ] **Step 5: Run + commit**

Run: `uv run pytest tests/test_migration_multi_org.py -v`
Expected: PASS.

```bash
git add src/daemon/migration_multi_org.py src/cli.py tests/test_migration_multi_org.py
git commit -m "feat(migration): opc migrate-to-multi-org

Converts a v1 single-org runtime into a v2 multi-org container with
one org subfolder. TTY-gated, --i-have-a-backup mandatory, refuses
with active tasks or open talks."
```

---

## Phase 8: Integration tests

Goal: prove parallel execution and migration work end-to-end with real subprocesses.

### Task 24: Two-orgs-concurrent integration test

**Files:**
- Create: `tests/integration/test_two_orgs_concurrent.py`
- Possibly modify: `tests/integration/conftest.py` to support multi-org fixtures

- [ ] **Step 1: Write the test**

```python
"""End-to-end: two orgs each submit a task; both run to completion in parallel."""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def test_two_orgs_concurrent(daemon_under_test, fake_claude_path):
    """Submit a task to org A and org B within 100ms of each other; assert
    both complete and neither's audit log mentions the other's task id."""
    container = daemon_under_test.runtime_path

    # init two orgs
    subprocess.run(
        ["uv", "run", "opc", "orgs", "init", "alpha"],
        check=True, env=daemon_under_test.env,
    )
    subprocess.run(
        ["uv", "run", "opc", "orgs", "init", "beta"],
        check=True, env=daemon_under_test.env,
    )

    # bootstrap a minimal agent in each (use fake-claude bootstrap helper)
    daemon_under_test.bootstrap_minimal_agent(slug="alpha")
    daemon_under_test.bootstrap_minimal_agent(slug="beta")

    # submit two tasks ~simultaneously
    proc_a = subprocess.Popen(
        ["uv", "run", "opc", "run", "--org", "alpha", "--brief", "ping"],
        env=daemon_under_test.env, stdout=subprocess.PIPE,
    )
    proc_b = subprocess.Popen(
        ["uv", "run", "opc", "run", "--org", "beta", "--brief", "ping"],
        env=daemon_under_test.env, stdout=subprocess.PIPE,
    )
    out_a, _ = proc_a.communicate(timeout=30)
    out_b, _ = proc_b.communicate(timeout=30)

    # poll task status until both terminal
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        status_a = daemon_under_test.task_status(slug="alpha", task_id="TASK-001")
        status_b = daemon_under_test.task_status(slug="beta", task_id="TASK-001")
        if status_a in ("completed", "failed") and status_b in ("completed", "failed"):
            break
        time.sleep(0.5)

    # assert isolation
    audit_a = daemon_under_test.audit_log(slug="alpha")
    audit_b = daemon_under_test.audit_log(slug="beta")
    for entry in audit_a:
        assert entry["task_id"].startswith("TASK-") or entry["task_id"] is None
        # No entry should reference an org-B-namespaced ID.
    # Each org has its own TASK-001 and they don't bleed.
    assert any(e["task_id"] == "TASK-001" for e in audit_a)
    assert any(e["task_id"] == "TASK-001" for e in audit_b)
```

- [ ] **Step 2: Add `bootstrap_minimal_agent` and helpers to `daemon_under_test` fixture**

`tests/integration/conftest.py` already exposes the daemon under test. Extend it with:
- `bootstrap_minimal_agent(slug: str)` — uses `opc orgs init <slug> --from <minimal-test-org-tree>` where the tree contains exactly one engineering_head agent (claude executor) configured to point at the fake binary.
- `task_status(slug, task_id)` — calls `/api/v1/orgs/{slug}/tasks/{task_id}` and returns the status field.
- `audit_log(slug)` — calls `/api/v1/orgs/{slug}/audit`.

Add a small `tests/integration/orgs/minimal/` test-only org tree (charter.md, escalation-rules.md, teams.yaml with one team, one EH agent file referencing the fake claude binary).

- [ ] **Step 3: Run**

Run: `uv run pytest tests/integration/test_two_orgs_concurrent.py -v -m integration`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_two_orgs_concurrent.py tests/integration/conftest.py tests/integration/orgs/
git commit -m "test(integration): two orgs run tasks concurrently in one daemon"
```

---

### Task 25: Migration end-to-end integration test

**Files:**
- Create: `tests/integration/test_migration_multi_org_e2e.py`

- [ ] **Step 1: Write the test**

```python
"""End-to-end: build a v1-shape runtime, run migration, daemon serves it."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def test_migrate_then_daemon_serves(tmp_path: Path):
    rt = tmp_path / "legacy"
    _build_v1_runtime_fixture(rt, slug="hk-tourism")

    subprocess.run(
        ["uv", "run", "opc", "migrate-to-multi-org",
         str(rt), "--i-have-a-backup", "--apply"],
        input=b"y\n", check=True,
    )

    assert (rt / "orgs" / "hk-tourism" / "org" / "teams.yaml").is_file()

    # boot a daemon against this migrated runtime; submit a task
    # (uses the daemon-under-test fixture or a thin local helper)
    ...


def _build_v1_runtime_fixture(rt: Path, *, slug: str) -> None:
    """Build a minimal v1 runtime with one approved EH agent and an empty DB."""
    ...
```

- [ ] **Step 2: Run + commit**

Run: `uv run pytest tests/integration/test_migration_multi_org_e2e.py -v -m integration`
Expected: PASS.

```bash
git add tests/integration/test_migration_multi_org_e2e.py
git commit -m "test(integration): migrate v1 → v2 then daemon serves the migrated org"
```

---

## Phase 9: Documentation + final verification

### Task 26: Update `CLAUDE.md`

**Files:**
- Modify: `CLAUDE.md` (project root)

- [ ] **Step 1: Edit the relevant sections**

Update:
- Directory layout section — show `<runtime>/orgs/<slug>/{...}` with the new tree
- Configuration section — add `OPC_ORG_SLUG` env var
- "Running the Daemon + CLI" — every example becomes `--org <slug>`; new commands `opc orgs`, `opc orgs init`, `opc orgs unload`, `opc migrate-to-multi-org`
- "Org-per-runtime layout" → rename to "Multi-org runtime layout"
- Drop the "Session timeout resolution" agent/org/code section's reference to `<runtime>/org/config.yaml` and replace with `<runtime>/orgs/<slug>/org/config.yaml`

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude): multi-org layout + --org examples"
```

---

### Task 27: Final verification

- [ ] **Step 1: Run the full unit suite**

Run: `uv run pytest tests/ -v`
Expected: PASS (all tests).

- [ ] **Step 2: Run the integration suite**

Run: `uv run pytest tests/ -v -m integration`
Expected: PASS (including the two-orgs-concurrent and migration tests).

- [ ] **Step 3: Manual smoke test**

```bash
# Fresh container
mkdir -p /tmp/opc-smoke && cd /tmp/opc-smoke
uv run --project /Users/tangbz/projects/my-opc opc init /tmp/opc-smoke/runtime

# Two orgs from the example tree
uv run --project /Users/tangbz/projects/my-opc opc orgs init hk \
  --from /Users/tangbz/projects/my-opc/examples/orgs/hk-macau-tourism
uv run --project /Users/tangbz/projects/my-opc opc orgs init lisbon \
  --from /Users/tangbz/projects/my-opc/examples/orgs/hk-macau-tourism

# Bootstrap workspaces in each
uv run --project /Users/tangbz/projects/my-opc opc init-agent --org hk
uv run --project /Users/tangbz/projects/my-opc opc init-agent --org lisbon

# Submit a task to each in parallel
uv run --project /Users/tangbz/projects/my-opc opc run --org hk --brief "explore" &
uv run --project /Users/tangbz/projects/my-opc opc run --org lisbon --brief "explore" &
wait

# Watch them
uv run --project /Users/tangbz/projects/my-opc opc tasks --org hk
uv run --project /Users/tangbz/projects/my-opc opc tasks --org lisbon
```

Expected: both tasks make progress; `opc tasks --org hk` does NOT show lisbon's task; vice versa.

- [ ] **Step 4: Final commit (if any cleanup needed)**

```bash
git status
# If clean, no commit needed.
```

---

## Spec coverage check

- §3 Architecture summary — Tasks 4, 5, 6, 7, 8, 9, 10–14
- §4 Filesystem layout — Tasks 1, 8 (skeleton), 23 (migration produces it)
- §5 Daemon state — Tasks 2, 4, 6
- §6 HTTP routing — Tasks 7–14
- §7 CLI surface — Tasks 18–22
- §8 Task and talk IDs — covered by per-org DB isolation (Tasks 2, 4, 24)
- §9 Migration — Tasks 23, 25
- §10 Testing approach — Tasks 24, 25, 27 (full suite)

## Done

After Task 27, the daemon serves multiple orgs in parallel from one runtime container. The two-orgs-concurrent integration test is the new red line for any future regression to a global lock or shared queue.
