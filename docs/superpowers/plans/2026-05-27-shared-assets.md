# Shared Assets Implementation Plan

> **2026-06-02 note:** This feature was renamed to "artifacts" — see
> `docs/superpowers/plans/2026-06-01-rename-assets-to-artifacts.md`. The
> design below is otherwise current; substitute `assets` → `artifacts`,
> `AssetStore` → `ArtifactStore`, `asset_put` → `artifact_put` when reading.

> **2026-06-14 note (TASK-305 — nested-key support):** Flat namespace only
> was the v1 constraint below. Nested keys with '/' path separators are now
> supported; the per-segment char set is still `[A-Za-z0-9._-]+`.

> **2026-06-10 note (Scope B — THR-007, TASK-070):** Delete, listed as
> out-of-scope for v1 below, has since shipped: `DELETE /artifacts/{name}`
> (`ArtifactStore.delete` + `AuditLogger.log_artifact_delete`, action
> `artifact_delete`, same `artifact:<name>` audit scope as `artifact_put`),
> surfaced as a confirm-gated delete control in the founder web artifacts UI.
> Update (PUT/PATCH) remains out of scope — `POST` is an idempotent
> create-or-overwrite. There is **no** CLI `artifacts delete` verb; delete is
> web + daemon route only.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an org-shared `assets/` directory where any agent can deposit persistent artifacts (reports, exports, screenshots, PDFs) that survive across tasks and are visible to every other agent in the same org.

**Architecture:**
- Org-scoped flat directory at `<runtime>/orgs/<slug>/assets/`. One folder per org; no nesting v1.
- Access exclusively via daemon HTTP routes + `happyranch assets {put,list,get}` CLI. Direct filesystem writes are blocked by Codex `workspace-write` sandbox and Opencode bash deny-by-default; the `happyranch` prefix is the only access path that works uniformly across Claude / Codex / Opencode executors.
- Mirrors the KB pattern (file-backed, atomic writes, audited) but without metadata/frontmatter — assets are opaque blobs.
- All three executor adapters get a new bootstrap section so every dispatched agent learns the folder's purpose and CLI on its next task.

**Tech Stack:** Python 3.13, FastAPI, httpx, pytest, existing daemon/CLI/audit infrastructure.

---

## Design Notes (read before starting)

**Why CLI-only:**
- Codex agents run under `sandbox_workspace_write` whose cwd is `workspaces/<agent>/`; writes to a sibling `assets/` are denied.
- Opencode agents have `opencode.json` `permission.bash: {"*": "deny", "happyranch *": "allow", ...}`; raw `cp`/`mv` are denied.
- Only `happyranch` is in the baseline allow-rule for every agent. Wrapping asset ops in `happyranch assets ...` is the only design that works for all three executors without per-executor permission gymnastics.

**Validation rules:**
- Name: matches `^[A-Za-z0-9._-]+$`, length 1-200, does not start with `.`, does not contain `..` or `/`.
- Size: max 10 MB per file (`MAX_ASSET_BYTES = 10 * 1024 * 1024`). Larger uploads → HTTP 413.
- Overwrite: PUT is idempotent — overwrites if name exists. No version history v1.

**Out of scope for v1 (explicitly):**
- Delete (`rm`) — founder can filesystem-delete if needed; defer until there's a real need. _(Shipped in Scope B / THR-007 as a daemon `DELETE` route + web UI control — see the 2026-06-10 note at the top. Still no CLI `rm` verb.)_
- Subdirectories / nested paths — flat namespace only. Agents can encode structure in the name (`cx-2026-05-27-report.pdf`).
- Search / prefix filtering — `list` returns all; agents can grep client-side.
- Web UI — `web/src/lib/api/assets.ts` not created; routes go in the OpenAPI `EXCLUDED_PATHS` set with reason "agent-facing v1, founder UI later".
- Per-agent attribution beyond a string field on the audit row — no quotas, no enforcement.

**Audit shape:**
- `asset_put` event only. Payload: `{name, size_bytes}`. Reads + lists are not audited (free).
- One new method on `AuditLogger`: `log_asset_put(name: str, size_bytes: int, agent: str)` — agent attribution lives in the `audit_log.agent` column (NOT duplicated in the payload, matching `log_talk_started` and `log_script_submitted`).
- The `audit_log.task_id` column is `TEXT NOT NULL`. Following the same overload pattern as `log_talk_started` (uses `talk_id`) and script-request audits (uses `SR-NNN`), `log_asset_put` stores `f"asset:{name}"` in the `task_id` column. The `asset:` prefix is **mandatory** — asset names are user-controlled and would otherwise collide with `TASK-NNN`/`TALK-NNN`/`SR-NNN` scopes consumed by `Database.get_audit_logs(task_id)`. There is no separate "assets" audit table.
- The real `AuditLogger` writes via `self._db.insert_audit_log(task_id=..., agent=..., action=..., payload=...)`. There is no `_write` wrapper. The DB column is `action` (not `event_type`); the public reader is `Database.get_audit_logs_by_action(action: str)`.

**Auth:**
- Every existing org-scoped router (`kb`, `tasks`, `talks`, `scripts`, ...) is mounted with `router = APIRouter(dependencies=[require_token()])` from `src.daemon.auth`. The assets router MUST do the same — without it, `/api/v1/orgs/{slug}/assets*` would be the only unauthenticated org-scoped surface.
- Org resolution: use `OrgDep = Annotated[OrgState, Depends(resolve_org)]` from `src/daemon/routes/_org_dep.py`. Do NOT pull `state.orgs.get(slug)` manually — `OrgDep` already raises `404 unknown_org` and is what KB / tasks / talks all use.
- `OrgState` does NOT expose `.audit_logger`. The convention in `routes/talks.py` is `AuditLogger(org.db).log_…(…)` constructed on demand. Match that.

**File structure (responsibility per file):**
- `src/orchestrator/_paths.py` — add `assets_dir` property (single source of truth for the path).
- `src/infrastructure/asset_store.py` (NEW) — pure storage logic: name validation, atomic writes, list, read, exists. No HTTP, no audit.
- `src/daemon/routes/assets.py` (NEW) — three FastAPI routes. Thin layer over `AssetStore` + audit call.
- `src/daemon/app.py` — register the router + ensure dir at lifespan startup for existing orgs.
- `src/daemon/routes/orgs.py` — mkdir at fresh-org init.
- `src/infrastructure/audit_logger.py` — one new method.
- `src/client/...` + `src/cli.py` — HTTP client + three CLI subcommands.
- `src/orchestrator/workspace_adapters.py` — bootstrap section helper + insertion into all three adapters' `_build_sections` (or equivalent).
- `tests/test_asset_store.py` — unit tests for storage layer.
- `tests/daemon/test_assets_routes.py` — unit tests for routes (TestClient).
- `tests/integration/test_assets_e2e.py` — end-to-end via real daemon + fake CLI.
- `tests/contract/openapi.json` — regenerated snapshot.
- `web/src/test/openapi-coverage.test.ts` — add four new paths to `EXCLUDED_PATHS`.
- `CLAUDE.md` — Directory Layout + new "Shared Assets" section.

---

## Task 1: Spec — paths property

**Files:**
- Modify: `src/orchestrator/_paths.py`
- Test: `tests/test_paths.py` (create if absent; otherwise append)

- [ ] **Step 1: Write failing test**

Append to `tests/test_paths.py` (create with this content if the file doesn't exist):

```python
from __future__ import annotations

from pathlib import Path

from src.orchestrator._paths import OrgPaths


def test_assets_dir_is_under_root(tmp_path: Path) -> None:
    paths = OrgPaths(root=tmp_path)
    assert paths.assets_dir == tmp_path / "assets"
```

- [ ] **Step 2: Run test, expect failure**

```bash
uv run pytest tests/test_paths.py::test_assets_dir_is_under_root -v
```

Expected: `AttributeError: 'OrgPaths' object has no attribute 'assets_dir'`.

- [ ] **Step 3: Add property to `src/orchestrator/_paths.py`**

After the existing `db_path` property (line 38), append:

```python
    @property
    def assets_dir(self) -> Path:
        return self.root / "assets"
```

- [ ] **Step 4: Re-run test, expect PASS**

```bash
uv run pytest tests/test_paths.py::test_assets_dir_is_under_root -v
```

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/_paths.py tests/test_paths.py
git commit -m "feat(assets): add OrgPaths.assets_dir property"
```

---

## Task 2: AssetStore — storage layer

**Files:**
- Create: `src/infrastructure/asset_store.py`
- Create: `tests/test_asset_store.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_asset_store.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from src.infrastructure.asset_store import (
    AssetStore,
    AssetTooLarge,
    InvalidAssetName,
    AssetNotFound,
    MAX_ASSET_BYTES,
)


def test_put_creates_file(tmp_path: Path) -> None:
    store = AssetStore(tmp_path / "assets")
    info = store.put("report.pdf", b"hello world")
    assert info.name == "report.pdf"
    assert info.size_bytes == 11
    assert (tmp_path / "assets" / "report.pdf").read_bytes() == b"hello world"


def test_put_overwrites_existing(tmp_path: Path) -> None:
    store = AssetStore(tmp_path / "assets")
    store.put("x.txt", b"first")
    info = store.put("x.txt", b"second")
    assert info.size_bytes == 6
    assert (tmp_path / "assets" / "x.txt").read_bytes() == b"second"


def test_put_is_atomic_on_failure(tmp_path: Path, monkeypatch) -> None:
    store = AssetStore(tmp_path / "assets")
    store.put("x.txt", b"original")

    # Force os.replace to fail; assert original survives, no .tmp file lingers.
    import os
    real_replace = os.replace

    def boom(_src, _dst):
        raise RuntimeError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(RuntimeError):
        store.put("x.txt", b"new")

    assert (tmp_path / "assets" / "x.txt").read_bytes() == b"original"
    # No stray temp files
    leftovers = [p.name for p in (tmp_path / "assets").iterdir() if p.name.startswith(".tmp")]
    assert leftovers == []
    monkeypatch.setattr(os, "replace", real_replace)


@pytest.mark.parametrize("bad_name", [
    "",
    ".",
    "..",
    ".hidden",
    "../escape",
    "with/slash",
    "with\\back",
    "with space",
    "a" * 201,
])
def test_put_rejects_invalid_names(tmp_path: Path, bad_name: str) -> None:
    store = AssetStore(tmp_path / "assets")
    with pytest.raises(InvalidAssetName):
        store.put(bad_name, b"x")


def test_put_rejects_oversized(tmp_path: Path) -> None:
    store = AssetStore(tmp_path / "assets")
    with pytest.raises(AssetTooLarge):
        store.put("big.bin", b"x" * (MAX_ASSET_BYTES + 1))


def test_put_accepts_exact_max(tmp_path: Path) -> None:
    store = AssetStore(tmp_path / "assets")
    info = store.put("max.bin", b"x" * MAX_ASSET_BYTES)
    assert info.size_bytes == MAX_ASSET_BYTES


def test_get_returns_bytes(tmp_path: Path) -> None:
    store = AssetStore(tmp_path / "assets")
    store.put("a.txt", b"content")
    assert store.read("a.txt") == b"content"


def test_get_missing_raises(tmp_path: Path) -> None:
    store = AssetStore(tmp_path / "assets")
    with pytest.raises(AssetNotFound):
        store.read("missing.txt")


def test_get_rejects_invalid_name(tmp_path: Path) -> None:
    store = AssetStore(tmp_path / "assets")
    with pytest.raises(InvalidAssetName):
        store.read("../etc/passwd")


def test_list_returns_sorted_summaries(tmp_path: Path) -> None:
    store = AssetStore(tmp_path / "assets")
    store.put("b.txt", b"22")
    store.put("a.txt", b"1")
    store.put("c.txt", b"333")
    names = [s.name for s in store.list_assets()]
    assert names == ["a.txt", "b.txt", "c.txt"]
    sizes = {s.name: s.size_bytes for s in store.list_assets()}
    assert sizes == {"a.txt": 1, "b.txt": 2, "c.txt": 3}


def test_list_skips_dotfiles_and_tmp(tmp_path: Path) -> None:
    store = AssetStore(tmp_path / "assets")
    store.put("real.txt", b"x")
    # Plant a dotfile + a stale tmp directly on disk
    (tmp_path / "assets" / ".DS_Store").write_bytes(b"")
    (tmp_path / "assets" / ".tmp.abc123").write_bytes(b"")
    names = [s.name for s in store.list_assets()]
    assert names == ["real.txt"]


def test_path_for_returns_resolved_path(tmp_path: Path) -> None:
    store = AssetStore(tmp_path / "assets")
    store.put("a.txt", b"x")
    p = store.path_for("a.txt")
    assert p == tmp_path / "assets" / "a.txt"
    assert p.exists()
```

- [ ] **Step 2: Run tests, expect import failure**

```bash
uv run pytest tests/test_asset_store.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.infrastructure.asset_store'`.

- [ ] **Step 3: Implement `src/infrastructure/asset_store.py`**

```python
"""Org-shared asset storage. Flat directory of opaque blobs.

Persistent artifacts produced by agents (reports, exports, screenshots, PDFs)
live here. Visible to every agent in the org via `happyranch assets {put,list,get}`.

This module owns name validation, atomic writes, and read/list. It does NOT
touch HTTP, audit, or agent identity — those are concerns of the route layer.
"""
from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


MAX_ASSET_BYTES = 10 * 1024 * 1024  # 10 MB hard cap per file (v1).
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_MAX_NAME_LEN = 200


class InvalidAssetName(ValueError):
    """Name fails validation rules."""


class AssetTooLarge(ValueError):
    """Payload exceeds MAX_ASSET_BYTES."""


class AssetNotFound(KeyError):
    """No asset with that name exists in the store."""


@dataclass(frozen=True, slots=True)
class AssetInfo:
    name: str
    size_bytes: int
    modified_at: str  # ISO-8601 UTC, "Z"-suffixed


class AssetStore:
    """File-backed flat blob store. Single directory; no nesting."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def validate_name(self, name: str) -> None:
        if not name or len(name) > _MAX_NAME_LEN:
            raise InvalidAssetName(f"invalid_name: {name!r}")
        if name.startswith(".") or ".." in name or "/" in name or "\\" in name:
            raise InvalidAssetName(f"invalid_name: {name!r}")
        if not _NAME_RE.match(name):
            raise InvalidAssetName(f"invalid_name: {name!r}")

    def path_for(self, name: str) -> Path:
        self.validate_name(name)
        return self._root / name

    def put(self, name: str, content: bytes) -> AssetInfo:
        self.validate_name(name)
        if len(content) > MAX_ASSET_BYTES:
            raise AssetTooLarge(f"asset_too_large: {len(content)}B > {MAX_ASSET_BYTES}B")
        target = self._root / name
        # Atomic write: write to .tmp.* sibling, then os.replace into place.
        fd, tmp_path_str = tempfile.mkstemp(prefix=".tmp.", dir=str(self._root))
        tmp_path = Path(tmp_path_str)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(content)
            os.replace(tmp_path, target)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
        stat = target.stat()
        return AssetInfo(
            name=name,
            size_bytes=stat.st_size,
            modified_at=_iso(stat.st_mtime),
        )

    def read(self, name: str) -> bytes:
        path = self.path_for(name)
        if not path.exists():
            raise AssetNotFound(name)
        return path.read_bytes()

    def exists(self, name: str) -> bool:
        try:
            return self.path_for(name).exists()
        except InvalidAssetName:
            return False

    def list_assets(self) -> list[AssetInfo]:
        out: list[AssetInfo] = []
        for entry in sorted(self._root.iterdir()):
            if entry.name.startswith(".") or not entry.is_file():
                continue
            try:
                self.validate_name(entry.name)
            except InvalidAssetName:
                continue
            stat = entry.stat()
            out.append(AssetInfo(
                name=entry.name,
                size_bytes=stat.st_size,
                modified_at=_iso(stat.st_mtime),
            ))
        return out


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
```

- [ ] **Step 4: Run tests, expect PASS**

```bash
uv run pytest tests/test_asset_store.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/asset_store.py tests/test_asset_store.py
git commit -m "feat(assets): add AssetStore with name validation + atomic writes"
```

---

## Task 3: Audit logger method

**Files:**
- Modify: `src/infrastructure/audit_logger.py`
- Test: `tests/test_audit_logger.py` (append; or create if absent)

- [ ] **Step 1: Inspect existing log_* methods to copy the pattern**

```bash
grep -n "def log_talk_started\|def log_talk_resumed" -A 8 src/infrastructure/audit_logger.py
```

The shape every method uses is:

```python
self._db.insert_audit_log(
    task_id=...,
    agent=...,
    action="...",
    payload={...},
)
```

There is **no** `_write` helper. `audit_log.task_id` is `TEXT NOT NULL`, so org-scoped events without a task overload the column with their resource id — `log_talk_started` puts `talk_id` there; script-request audits put `SR-NNN`. For assets, we put `f"asset:{name}"` (the `asset:` prefix is mandatory to avoid collision with `TASK-NNN`/`TALK-NNN`/`SR-NNN` scopes).

- [ ] **Step 2: Add failing test**

In `tests/test_audit_logger.py`, append:

```python
def test_log_asset_put_writes_event(tmp_path) -> None:
    # Adjust import + AuditLogger construction to match the file's existing fixtures.
    from src.infrastructure.database import Database
    from src.infrastructure.audit_logger import AuditLogger

    db = Database(tmp_path / "test.db")
    db.init_schema()
    logger = AuditLogger(db)
    logger.log_asset_put(name="report.pdf", size_bytes=11, agent="dev_agent")

    rows = db.get_audit_logs_by_action("asset_put")
    assert len(rows) == 1
    row = rows[0]
    assert row["task_id"] == "asset:report.pdf"  # namespaced to avoid collision with TASK-/TALK-/SR- ids
    assert row["agent"] == "dev_agent"
    assert row["action"] == "asset_put"
    assert row["payload"] == {"name": "report.pdf", "size_bytes": 11}
```

*Note:* if the existing test file uses different fixtures (in-memory DB, factory, etc.), match them. Read the top of `tests/test_audit_logger.py` first.

- [ ] **Step 3: Run, expect failure**

```bash
uv run pytest tests/test_audit_logger.py::test_log_asset_put_writes_event -v
```

- [ ] **Step 4: Implement**

In `src/infrastructure/audit_logger.py`, add near the `log_talk_started` / `log_talk_resumed` block (since those are the closest peers — both are resource-scoped events that overload `task_id`):

```python
    def log_asset_put(self, name: str, size_bytes: int, agent: str) -> None:
        self._db.insert_audit_log(
            task_id=f"asset:{name}",  # namespaced to avoid collision with TASK-/TALK-/SR- ids in get_audit_logs(task_id)
            agent=agent,
            action="asset_put",
            payload={"name": name, "size_bytes": size_bytes},  # agent column is the source of truth for attribution
        )
```

- [ ] **Step 5: Run, expect PASS**

```bash
uv run pytest tests/test_audit_logger.py::test_log_asset_put_writes_event -v
```

- [ ] **Step 6: Commit**

```bash
git add src/infrastructure/audit_logger.py tests/test_audit_logger.py
git commit -m "feat(assets): add AuditLogger.log_asset_put"
```

---

## Task 4: Daemon routes

**Files:**
- Create: `src/daemon/routes/assets.py`
- Modify: `src/daemon/app.py` (router registration)
- Modify: `src/daemon/routes/orgs.py` (mkdir at init)
- Create: `tests/daemon/test_assets_routes.py`

- [ ] **Step 1: Read existing router pattern**

```bash
sed -n '1,50p' src/daemon/routes/kb.py
```

Note the imports, `router = APIRouter(...)`, dependency-injection of `DaemonState` / org lookup, and error-shape conventions (`HTTPException(status_code=..., detail={"code": ..., "message": ...})`).

- [ ] **Step 2: Write failing route tests**

Create `tests/daemon/test_assets_routes.py`. Match the fixture style used by `tests/daemon/test_kb_routes.py` (read it first):

```python
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# Reuse the project's TestClient fixture pattern; adjust the import to match.
# Below assumes a `client_with_org(slug)` factory similar to other route tests.

def test_put_creates_asset(client_with_org, slug: str = "demo") -> None:
    client, org_root = client_with_org(slug)
    r = client.post(
        f"/api/v1/orgs/{slug}/assets",
        params={"name": "report.pdf", "agent": "dev_agent"},
        files={"file": ("report.pdf", b"hello world", "application/pdf")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "report.pdf"
    assert body["size_bytes"] == 11
    assert (org_root / "assets" / "report.pdf").read_bytes() == b"hello world"


def test_put_uses_uploaded_filename_when_name_omitted(client_with_org) -> None:
    client, org_root = client_with_org("demo")
    r = client.post(
        "/api/v1/orgs/demo/assets",
        params={"agent": "dev_agent"},
        files={"file": ("uploaded.bin", b"abc", "application/octet-stream")},
    )
    assert r.status_code == 200
    assert r.json()["name"] == "uploaded.bin"


def test_put_rejects_invalid_name(client_with_org) -> None:
    client, _ = client_with_org("demo")
    r = client.post(
        "/api/v1/orgs/demo/assets",
        params={"name": "../escape", "agent": "dev_agent"},
        files={"file": ("x", b"x", "application/octet-stream")},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_asset_name"


def test_put_rejects_oversized(client_with_org) -> None:
    client, _ = client_with_org("demo")
    from src.infrastructure.asset_store import MAX_ASSET_BYTES
    big = b"x" * (MAX_ASSET_BYTES + 1)
    r = client.post(
        "/api/v1/orgs/demo/assets",
        params={"name": "big.bin", "agent": "dev_agent"},
        files={"file": ("big.bin", big, "application/octet-stream")},
    )
    assert r.status_code == 413
    assert r.json()["detail"]["code"] == "asset_too_large"


def test_list_returns_summaries(client_with_org) -> None:
    client, _ = client_with_org("demo")
    client.post("/api/v1/orgs/demo/assets",
                params={"name": "a.txt", "agent": "x"},
                files={"file": ("a.txt", b"1", "text/plain")})
    client.post("/api/v1/orgs/demo/assets",
                params={"name": "b.txt", "agent": "x"},
                files={"file": ("b.txt", b"22", "text/plain")})
    r = client.get("/api/v1/orgs/demo/assets")
    assert r.status_code == 200
    items = r.json()["assets"]
    assert [a["name"] for a in items] == ["a.txt", "b.txt"]


def test_get_returns_bytes(client_with_org) -> None:
    client, _ = client_with_org("demo")
    client.post("/api/v1/orgs/demo/assets",
                params={"name": "a.txt", "agent": "x"},
                files={"file": ("a.txt", b"contents", "text/plain")})
    r = client.get("/api/v1/orgs/demo/assets/a.txt")
    assert r.status_code == 200
    assert r.content == b"contents"


def test_get_missing_returns_404(client_with_org) -> None:
    client, _ = client_with_org("demo")
    r = client.get("/api/v1/orgs/demo/assets/missing.txt")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "asset_not_found"


def test_put_writes_audit_event(client_with_org) -> None:
    client, org_root = client_with_org("demo")
    client.post("/api/v1/orgs/demo/assets",
                params={"name": "x.txt", "agent": "dev_agent"},
                files={"file": ("x.txt", b"hi", "text/plain")})
    # Inspect the org's audit log — the /audit route's filter param is `action`
    r = client.get("/api/v1/orgs/demo/audit", params={"action": "asset_put"})
    assert r.status_code == 200
    entries = r.json().get("entries", [])
    assert any(e.get("payload", {}).get("name") == "x.txt" for e in entries)
```

*If `client_with_org` doesn't exist as a fixture, build the TestClient inline using the same approach as `tests/daemon/test_kb_routes.py`. Do NOT invent a fixture surface — match what exists.*

- [ ] **Step 3: Run, expect failure**

```bash
uv run pytest tests/daemon/test_assets_routes.py -v
```

Expected: 404 on all routes (no router registered yet).

- [ ] **Step 4: Implement `src/daemon/routes/assets.py`**

```python
"""Org-shared assets routes. Flat blob store, atomic writes, audited puts.

Auth: same bearer token as every other org-scoped route. No per-agent
authorization — any agent that can hit the daemon can put/list/get.
"""
from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from src.daemon.auth import require_token
from src.daemon.org_state import OrgState
from src.daemon.routes._org_dep import OrgDep
from src.infrastructure.asset_store import (
    AssetStore,
    AssetTooLarge,
    InvalidAssetName,
    MAX_ASSET_BYTES,
)
from src.infrastructure.audit_logger import AuditLogger

# Token-required for every endpoint — matches kb/tasks/talks/scripts routers.
router = APIRouter(dependencies=[require_token()])


def _store(org: OrgState) -> AssetStore:
    return AssetStore(org.root / "assets")


@router.post("/assets")
async def put_asset(
    slug: str,
    org: OrgDep,
    file: UploadFile = File(...),
    name: str | None = Query(None),
    agent: str = Query(...),
) -> dict:
    content = await file.read()
    if len(content) > MAX_ASSET_BYTES:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "asset_too_large",
                "max_bytes": MAX_ASSET_BYTES,
                "size_bytes": len(content),
            },
        )
    effective_name = name or file.filename or ""
    try:
        info = _store(org).put(effective_name, content)
    except InvalidAssetName as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_asset_name", "name": effective_name, "message": str(exc)},
        ) from exc
    except AssetTooLarge as exc:
        raise HTTPException(
            status_code=413,
            detail={"code": "asset_too_large", "max_bytes": MAX_ASSET_BYTES},
        ) from exc

    # Construct AuditLogger on demand — matches the routes/talks.py pattern.
    AuditLogger(org.db).log_asset_put(
        name=info.name,
        size_bytes=info.size_bytes,
        agent=agent,
    )

    return {
        "name": info.name,
        "size_bytes": info.size_bytes,
        "modified_at": info.modified_at,
    }


@router.get("/assets")
async def list_assets(slug: str, org: OrgDep) -> dict:
    return {
        "assets": [
            {"name": a.name, "size_bytes": a.size_bytes, "modified_at": a.modified_at}
            for a in _store(org).list_assets()
        ],
    }


@router.get("/assets/{name}")
async def get_asset(slug: str, name: str, org: OrgDep) -> FileResponse:
    try:
        path = _store(org).path_for(name)
    except InvalidAssetName as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_asset_name", "name": name, "message": str(exc)},
        ) from exc
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail={"code": "asset_not_found", "name": name},
        )
    return FileResponse(path=str(path), filename=name)
```

- [ ] **Step 5: Register the router**

In `src/daemon/app.py`, after line 114 (the `scripts.router` registration), add:

```python
    from src.daemon.routes import assets
    app.include_router(assets.router, prefix="/api/v1/orgs/{slug}", tags=["assets"])
```

(Match the existing import style — if other routes are imported at module top, put `assets` there instead.)

- [ ] **Step 6: mkdir at fresh-org init**

In `src/daemon/routes/orgs.py` `_seed_skeleton` (line 50 area), after the `talks` mkdir:

```python
    (org_root / "assets").mkdir(exist_ok=True)
```

- [ ] **Step 7: Lifespan ensure for existing orgs**

In `src/daemon/app.py`, find the lifespan startup loop that iterates orgs (search for `state.orgs` or `recover_orphaned_running_scripts` — the same loop). Add an idempotent:

```python
    (org.root / "assets").mkdir(exist_ok=True)
```

- [ ] **Step 8: Run all new tests**

```bash
uv run pytest tests/daemon/test_assets_routes.py -v
```

- [ ] **Step 9: Run regressions on touched files**

```bash
uv run pytest tests/daemon/test_orgs_routes.py tests/daemon/test_kb_routes.py -v
```

- [ ] **Step 10: Commit**

```bash
git add src/daemon/routes/assets.py src/daemon/app.py src/daemon/routes/orgs.py tests/daemon/test_assets_routes.py
git commit -m "feat(assets): add daemon routes (put/list/get) + dir init"
```

---

## Task 5: CLI surface

**Files:**
- Modify: `src/client/...` (HTTP client method file — find via grep)
- Modify: `src/cli.py`
- Test: `tests/test_cli_assets.py` (create)

- [ ] **Step 1: Find existing CLI command + client pattern**

```bash
grep -n "def cmd_kb\|def kb_add\|kb_list\|kb_get" src/cli.py src/client/*.py | head -20
```

Read `cmd_kb_add` and the matching client method end-to-end. Mirror its structure exactly.

- [ ] **Step 2: Write failing CLI test**

Create `tests/test_cli_assets.py`:

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Adjust imports to match the project's CLI test fixtures.


def test_cli_assets_put_invokes_client(tmp_path: Path, monkeypatch) -> None:
    from src import cli as cli_module

    local = tmp_path / "report.pdf"
    local.write_bytes(b"hello")

    mock_client = MagicMock()
    mock_client.put_asset.return_value = {
        "name": "report.pdf", "size_bytes": 5, "modified_at": "2026-05-27T00:00:00Z",
    }
    monkeypatch.setattr(cli_module, "build_client", lambda *a, **k: mock_client)

    rc = cli_module.main([
        "assets", "put", str(local),
        "--agent", "dev_agent",
        "--org", "demo",
    ])
    assert rc == 0
    mock_client.put_asset.assert_called_once_with(
        slug="demo",
        local_path=local,
        name=None,
        agent="dev_agent",
    )


def test_cli_assets_list_invokes_client(monkeypatch) -> None:
    from src import cli as cli_module
    mock_client = MagicMock()
    mock_client.list_assets.return_value = {"assets": [
        {"name": "a.txt", "size_bytes": 1, "modified_at": "2026-05-27T00:00:00Z"},
    ]}
    monkeypatch.setattr(cli_module, "build_client", lambda *a, **k: mock_client)
    rc = cli_module.main(["assets", "list", "--org", "demo"])
    assert rc == 0


def test_cli_assets_get_writes_to_output(tmp_path: Path, monkeypatch) -> None:
    from src import cli as cli_module
    out = tmp_path / "downloaded.bin"
    mock_client = MagicMock()
    mock_client.get_asset.return_value = b"contents"
    monkeypatch.setattr(cli_module, "build_client", lambda *a, **k: mock_client)
    rc = cli_module.main([
        "assets", "get", "a.txt",
        "--output", str(out),
        "--org", "demo",
    ])
    assert rc == 0
    assert out.read_bytes() == b"contents"
```

*Match the project's actual CLI test idiom — the `build_client` name + `main([...])` invocation above is a placeholder. Read an existing CLI test (e.g. for KB) and copy its shape.*

- [ ] **Step 3: Run, expect failure**

```bash
uv run pytest tests/test_cli_assets.py -v
```

- [ ] **Step 4: Add client methods**

In whichever client file holds `kb_add` / `kb_list` (likely `src/client/__init__.py` or similar), add:

```python
    def put_asset(self, *, slug: str, local_path: Path, name: str | None, agent: str) -> dict:
        with local_path.open("rb") as fh:
            files = {"file": (name or local_path.name, fh, "application/octet-stream")}
            params: dict[str, str] = {"agent": agent}
            if name:
                params["name"] = name
            r = self._client.post(
                f"/api/v1/orgs/{slug}/assets",
                files=files,
                params=params,
            )
        r.raise_for_status()
        return r.json()

    def list_assets(self, *, slug: str) -> dict:
        r = self._client.get(f"/api/v1/orgs/{slug}/assets")
        r.raise_for_status()
        return r.json()

    def get_asset(self, *, slug: str, name: str) -> bytes:
        r = self._client.get(f"/api/v1/orgs/{slug}/assets/{name}")
        r.raise_for_status()
        return r.content
```

- [ ] **Step 5: Add CLI subcommand group**

In `src/cli.py`, after the KB subparser block, add an `assets` subparser group with three subcommands (`put`, `list`, `get`). Match the parser-building style of the existing `kb` group exactly — argument order, naming, help strings, slug-resolution helper.

```python
# Pseudocode shape — adapt to actual cli.py style
def _add_assets_subparser(sp):
    assets = sp.add_parser("assets", help="Org-shared asset blobs (put/list/get).")
    asub = assets.add_subparsers(dest="assets_cmd", required=True)

    put_p = asub.add_parser("put", help="Upload a local file to the org's shared assets.")
    put_p.add_argument("local_path", type=Path)
    put_p.add_argument("--name", default=None, help="Override stored filename (default: local basename).")
    put_p.add_argument("--agent", required=True, help="Your agent name (for audit attribution).")
    _add_org_flag(put_p)

    list_p = asub.add_parser("list", help="List asset names + sizes.")
    _add_org_flag(list_p)

    get_p = asub.add_parser("get", help="Download an asset by name.")
    get_p.add_argument("name")
    get_p.add_argument("--output", type=Path, required=True, help="Local path to write the asset bytes to.")
    _add_org_flag(get_p)

def cmd_assets_put(args) -> int:
    client = build_client(...)
    info = client.put_asset(slug=resolve_slug(args), local_path=args.local_path, name=args.name, agent=args.agent)
    print(f"uploaded {info['name']} ({info['size_bytes']}B)")
    return 0

def cmd_assets_list(args) -> int:
    client = build_client(...)
    body = client.list_assets(slug=resolve_slug(args))
    for a in body["assets"]:
        print(f"{a['name']}\t{a['size_bytes']}\t{a['modified_at']}")
    return 0

def cmd_assets_get(args) -> int:
    client = build_client(...)
    data = client.get_asset(slug=resolve_slug(args), name=args.name)
    args.output.write_bytes(data)
    print(f"saved {len(data)}B to {args.output}")
    return 0
```

Dispatch from the main parser switch (alongside `kb` → `cmd_kb_*`).

- [ ] **Step 6: Run tests, expect PASS**

```bash
uv run pytest tests/test_cli_assets.py -v
```

- [ ] **Step 7: Smoke test against a live daemon**

```bash
scripts/daemon.sh start
echo "hello" > /tmp/hello.txt
uv run happyranch assets put /tmp/hello.txt --agent founder
uv run happyranch assets list
uv run happyranch assets get hello.txt --output /tmp/back.txt
diff /tmp/hello.txt /tmp/back.txt && echo "OK"
```

- [ ] **Step 8: Commit**

```bash
git add src/cli.py src/client/ tests/test_cli_assets.py
git commit -m "feat(assets): add happyranch assets {put,list,get} CLI"
```

---

## Task 6: Bootstrap doc — agent awareness

**Files:**
- Modify: `src/orchestrator/workspace_adapters.py`
- Test: `tests/test_workspace_adapters.py` (append; or create if absent)

The goal: every agent's `CLAUDE.md` / `AGENTS.md` (regenerated on every dispatch) carries a "Shared Assets" section so the agent knows the folder exists, its purpose, and the CLI to access it.

- [ ] **Step 1: Add failing test**

Append to `tests/test_workspace_adapters.py`:

```python
def test_claude_md_includes_shared_assets_section(tmp_path: Path) -> None:
    # Adjust adapter construction to match the existing test fixtures.
    from src.config import Settings
    from src.orchestrator._paths import OrgPaths
    from src.orchestrator.workspace_adapters import ClaudeWorkspaceAdapter

    paths = OrgPaths(root=tmp_path)
    adapter = ClaudeWorkspaceAdapter(Settings(), paths, slug="demo")
    workspace = tmp_path / "workspaces" / "dev_agent"
    adapter.write_claude_md(workspace, "dev_agent", "You are dev_agent.")
    content = (workspace / "CLAUDE.md").read_text()
    assert "## Shared Assets" in content
    assert "happyranch assets put" in content
    assert "happyranch assets list" in content
    assert "happyranch assets get" in content


def test_codex_agents_md_includes_shared_assets_section(tmp_path: Path) -> None:
    from src.config import Settings
    from src.orchestrator._paths import OrgPaths
    from src.orchestrator.workspace_adapters import CodexWorkspaceAdapter

    paths = OrgPaths(root=tmp_path)
    adapter = CodexWorkspaceAdapter(Settings(), paths, slug="demo")
    workspace = tmp_path / "workspaces" / "dev_agent"
    adapter.write_agents_md(workspace, "dev_agent", "You are dev_agent.")
    content = (workspace / "AGENTS.md").read_text()
    assert "## Shared Assets" in content
    assert "happyranch assets put" in content


def test_opencode_agents_md_includes_shared_assets_section(tmp_path: Path) -> None:
    from src.config import Settings
    from src.orchestrator._paths import OrgPaths
    from src.orchestrator.workspace_adapters import OpencodeWorkspaceAdapter

    paths = OrgPaths(root=tmp_path)
    adapter = OpencodeWorkspaceAdapter(Settings(), paths, slug="demo")
    workspace = tmp_path / "workspaces" / "dev_agent"
    adapter.write_agents_md(workspace, "dev_agent", "You are dev_agent.")
    content = (workspace / "AGENTS.md").read_text()
    assert "## Shared Assets" in content
    assert "happyranch assets put" in content
```

- [ ] **Step 2: Run, expect failure**

```bash
uv run pytest tests/test_workspace_adapters.py -v -k shared_assets
```

- [ ] **Step 3: Add the section helper**

In `src/orchestrator/workspace_adapters.py` near `_learnings_bootstrap_section` (around line 84), add:

```python
def _shared_assets_section() -> list[str]:
    return [
        "## Shared Assets (org-wide)\n",
        "Path: `<runtime>/orgs/<slug>/assets/`. Drop persistent artifacts your work",
        "produces — generated reports, exports, screenshots, PDFs, images. Files",
        "here survive across tasks and are visible to every agent in this org.\n",
        "Use cases: a generated PDF report another agent needs to attach to a",
        "customer reply; a CSV export the founder will want to review; a screenshot",
        "captured during QA that the bug-triage agent should see.\n",
        "**Not** the KB. KB is for durable cross-agent *knowledge* (rules,",
        "references, founder rulings). Assets are for *files and binary artifacts*.",
        "Don't put scratch work here — use your workspace `repos/`, learning",
        "entries, or task artifacts for transient state.\n",
        "All access is via `happyranch`. Direct filesystem reads/writes won't work",
        "uniformly across executors — use the CLI:\n",
        "```",
        "happyranch assets put <local-path> --agent <you> [--name <name>]",
        "happyranch assets list",
        "happyranch assets get <name> --output <local-path>",
        "```\n",
        "Naming convention: prefix with your agent name + ISO date for",
        "traceability, e.g. `dev_agent-2026-05-27-perf-report.pdf`. Names must",
        "match `[A-Za-z0-9._-]+`, max 200 chars. Per-file size cap: 10 MB.\n",
    ]
```

- [ ] **Step 4: Insert into `ClaudeWorkspaceAdapter._build_sections`**

In `_build_sections` (line ~337), after the KB callback note block and before the `## Task Recall` section, splice in `*_shared_assets_section()`:

```python
            callback_note + "\n",
            *_shared_assets_section(),
            "## Task Recall\n",
```

- [ ] **Step 5: Insert into Codex `write_agents_md`**

Read the Codex adapter's `write_agents_md` (line ~435). Find the equivalent section assembly. Insert `*_shared_assets_section()` at the same logical position (after KB block, before workflow / task recall block).

- [ ] **Step 6: Insert into Opencode `write_agents_md`**

Repeat for the Opencode adapter (line ~494). Same insertion point.

- [ ] **Step 7: Run all bootstrap tests + adapter regressions**

```bash
uv run pytest tests/test_workspace_adapters.py -v
```

- [ ] **Step 8: Sanity-rebuild for one workspace and read the output**

```bash
uv run pytest tests/test_workspace_adapters.py -v -k shared_assets
# Spot-check rendered output if a fixture exposes it.
```

- [ ] **Step 9: Commit**

```bash
git add src/orchestrator/workspace_adapters.py tests/test_workspace_adapters.py
git commit -m "feat(assets): add Shared Assets bootstrap section for all 3 executors"
```

---

## Task 7: Integration test

**Files:**
- Create: `tests/integration/test_assets_e2e.py`

- [ ] **Step 1: Read existing integration test pattern**

```bash
ls tests/integration/
sed -n '1,80p' tests/integration/test_threads_e2e.py
```

Understand the live-daemon + fake-CLI fixture shape.

- [ ] **Step 2: Write the end-to-end test**

Create `tests/integration/test_assets_e2e.py`:

```python
from __future__ import annotations

import pytest

# Match the markers + fixtures already used by other integration tests.
pytestmark = pytest.mark.integration


def test_put_list_get_roundtrip(live_daemon_with_org, tmp_path) -> None:
    client, org_root, slug = live_daemon_with_org("demo")

    local = tmp_path / "report.pdf"
    local.write_bytes(b"pdf-content-here")

    # PUT
    put_resp = client.post(
        f"/api/v1/orgs/{slug}/assets",
        params={"name": "report.pdf", "agent": "dev_agent"},
        files={"file": ("report.pdf", local.read_bytes(), "application/pdf")},
    )
    assert put_resp.status_code == 200

    # Disk-level assertion: file is where we said it would be
    assert (org_root / "assets" / "report.pdf").read_bytes() == b"pdf-content-here"

    # LIST sees it
    list_resp = client.get(f"/api/v1/orgs/{slug}/assets")
    assert list_resp.status_code == 200
    names = [a["name"] for a in list_resp.json()["assets"]]
    assert "report.pdf" in names

    # GET returns the bytes
    get_resp = client.get(f"/api/v1/orgs/{slug}/assets/report.pdf")
    assert get_resp.status_code == 200
    assert get_resp.content == b"pdf-content-here"

    # Audit row exists — /audit filter param is `action` (matches the DB column)
    audit_resp = client.get(f"/api/v1/orgs/{slug}/audit", params={"action": "asset_put"})
    assert any(
        e.get("payload", {}).get("name") == "report.pdf"
        for e in audit_resp.json().get("entries", [])
    )


def test_lifespan_creates_assets_dir_for_existing_org(live_daemon_with_org) -> None:
    """Pre-existing orgs without an assets/ dir should self-heal at startup."""
    client, org_root, slug = live_daemon_with_org("demo", skip_assets_dir=True)
    # If the fixture supports a flag, test the startup-recovery path.
    # Otherwise, delete the dir, restart the daemon, and assert it's recreated.
    assert (org_root / "assets").is_dir()
```

*If `live_daemon_with_org` doesn't accept `skip_assets_dir`, simplify the second test to: delete the `assets/` dir after `_seed_skeleton` runs, then verify a daemon-restart fixture re-creates it.*

- [ ] **Step 3: Run integration**

```bash
uv run pytest tests/integration/test_assets_e2e.py -v -m integration
```

- [ ] **Step 4: Run full integration suite to check for regressions**

```bash
uv run pytest tests/integration/ -v -m integration
```

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_assets_e2e.py
git commit -m "test(assets): add end-to-end put/list/get roundtrip + lifespan ensure"
```

---

## Task 8: OpenAPI snapshot + web coverage

**Files:**
- Modify: `tests/contract/openapi.json` (regenerated)
- Modify: `web/src/test/openapi-coverage.test.ts`

- [ ] **Step 1: Regenerate snapshot**

```bash
HAPPYRANCH_REGEN_OPENAPI=1 uv run pytest tests/contract/test_openapi_snapshot.py -v
```

- [ ] **Step 2: Verify the diff**

```bash
git diff tests/contract/openapi.json | head -80
```

Confirm exactly four new path entries:
- `POST /api/v1/orgs/{slug}/assets`
- `GET  /api/v1/orgs/{slug}/assets`
- `GET  /api/v1/orgs/{slug}/assets/{name}`

(That's 3 paths, 3 operations — the slash-prefix counter sees 3 unique paths.)

- [ ] **Step 3: Re-run snapshot test, expect PASS (idempotent)**

```bash
uv run pytest tests/contract/test_openapi_snapshot.py -v
```

- [ ] **Step 4: Update web coverage test**

In `web/src/test/openapi-coverage.test.ts`, find the `EXCLUDED_PATHS` set. Add the three new asset paths with a one-line reason:

```typescript
  // Assets — agent-facing v1 (CLI only). Founder UI deferred until needed.
  '/api/v1/orgs/{slug}/assets',
  '/api/v1/orgs/{slug}/assets/{name}',
```

(There are typically two entries for the collection path — one for POST and one for GET — but the test groups by path string, not by method. One entry per path.)

- [ ] **Step 5: Run web coverage test**

```bash
cd web && npm test -- openapi-coverage
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/contract/openapi.json web/src/test/openapi-coverage.test.ts
git commit -m "chore(assets): regen openapi snapshot + exclude assets paths from web mirror"
```

---

## Task 9: Project doc — CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update Directory Layout**

Find the runtime layout block (under "Directory Layout"). After the `talks/` line:

```
    |-- scripts/                       # SR-NNN.{out,err,script} (full captured output + frozen script body)
    `-- assets/                        # org-shared blob store (put/list/get via `happyranch assets`)
```

(Adjust the trailing-`-` characters to match the existing ASCII tree style.)

- [ ] **Step 2: Add a "Shared Assets" section**

After the "Per-Agent Learnings" section, insert:

```markdown
## Shared Assets (org-wide blob store)

Per-org at `<runtime>/orgs/<slug>/assets/`. Flat directory of opaque files —
persistent artifacts produced by any agent and visible to every other agent
in the same org. Implementation: `src/infrastructure/asset_store.py` +
`src/daemon/routes/assets.py`. CLI: `happyranch assets {put,list,get}`.

**Non-obvious invariants:**

- **CLI-only access by design** — Codex (`workspace-write` sandbox) and
  Opencode (bash deny-by-default) both block direct writes outside the
  agent's workspace; only the `happyranch` baseline allow-rule works across
  all three executors. Don't add a "just `cat`/`cp` it" agent skill.
- **Flat namespace; no nesting v1** — names match `[A-Za-z0-9._-]+`, max
  200 chars, no leading `.`. Slash-bearing names rejected as
  `invalid_asset_name`.
- **Size cap is 10 MB per file** (`MAX_ASSET_BYTES`). Larger uploads → HTTP
  413. v1 has no chunking / multipart resumption.
- **PUT is idempotent (overwrites)** — no version history; agents are
  expected to encode date/identity in the name if they care about
  history. Atomic via `tempfile.mkstemp` + `os.replace` so partial writes
  never leak.
- **`asset_put` is audited; `list`/`get` are not** — read paths are free,
  consistent with KB list/get and on the same rationale (no PII gradient
  inside the asset store).
- **Not the KB** — assets are blobs. The KB is for typed/structured
  knowledge (frontmatter, slug, type, topic). Don't dump markdown content
  into assets/ that should be a KB entry.
- **Dir created at fresh-org init AND idempotently at lifespan startup**
  for orgs that pre-date the feature. Both code paths are required.
```

- [ ] **Step 3: Spot-check rendered markdown**

```bash
git diff CLAUDE.md | head -60
```

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(assets): document Shared Assets layer in CLAUDE.md"
```

---

## Task 10: Final verification

- [ ] **Step 1: Full unit suite**

```bash
uv run pytest tests/ -v
```

Expected: all green. No new failures, no regressions.

- [ ] **Step 2: Full integration suite**

```bash
uv run pytest tests/ -v -m integration
```

Expected: green.

- [ ] **Step 3: Web tests**

```bash
cd web && npm test
```

Expected: green.

- [ ] **Step 4: Manual smoke against a real daemon**

```bash
scripts/daemon.sh stop || true
scripts/daemon.sh start
# Use whatever org slug is present in your local runtime
uv run happyranch assets put README.md --agent founder
uv run happyranch assets list
uv run happyranch assets get README.md --output /tmp/happyranch-readme-copy.md
diff README.md /tmp/happyranch-readme-copy.md && echo "OK roundtrip"

# Confirm audit row
uv run happyranch audit --action asset_put | head
```

- [ ] **Step 5: Re-index gitnexus**

```bash
npx gitnexus analyze --force --embeddings
```

- [ ] **Step 6: Final commit if anything trivial was tweaked during verification**

```bash
git status
# If clean: nothing to do. Otherwise commit any tail-end tweaks.
```

---

## Self-Review Checklist (run after completion)

- [ ] All `happyranch assets` invocations work for: `put` (founder), `put` (agent), `list`, `get`
- [ ] `assets/` directory exists for both newly-init'd orgs AND pre-existing orgs (post-daemon-restart)
- [ ] `asset_put` audit row visible via `happyranch audit`
- [ ] All three executor adapters' bootstrap docs contain the "Shared Assets" section (verify by inspecting `<runtime>/orgs/<slug>/workspaces/<agent>/CLAUDE.md` or `AGENTS.md`)
- [ ] OpenAPI snapshot test green; web openapi-coverage test green
- [ ] CLAUDE.md Directory Layout and Shared Assets section reflect the actual implementation
- [ ] No new `mkdir` calls outside of `_seed_skeleton` + lifespan recovery — single source of truth for dir creation
- [ ] `MAX_ASSET_BYTES` referenced in: store, route 413 detail, bootstrap doc — no magic-number drift
