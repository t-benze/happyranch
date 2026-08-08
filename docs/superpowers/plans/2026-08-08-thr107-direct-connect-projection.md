# THR-107 Slice 1: Direct-Connect Projection & Durable Completion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a merged, non-launchable `received_nonlaunchable` direct-connect receipt into a durably `COMMITTED`, launch-eligible custom-adapter executor profile — automatically, synchronously, with no founder approval step — so the product path becomes `Connect → Connected` for a new custom CLI.

**Architecture:** Add a transaction/compensation coordinator (`direct_connect_projection.py`) that runs a bounded conformance probe against the already hash-pinned wrapper, then reuses the EXISTING, battle-tested custom-adapter persistence primitives (`adapter_store.save_adapter`, `custom_adapter_registry._perform_adapter_profile_binding`) to write a durable `AdapterEntry(status="approved")` and bind a runtime executor profile — instead of inventing new profile/registry write paths. A new `direct_connect_projections` SQLite table (in the already-merged `DirectConnectAuthorityStore`) tracks `planned → committed | failed` per operation so retries are idempotent and crashes mid-projection never leave ambiguous state. The coordinator is invoked synchronously from the existing `POST /runtime/custom-cli/connect` handler, immediately after the existing (already-merged) `receive()` + token-commit calls, so Connect and Connected happen in one HTTP round trip.

**Tech Stack:** Python 3.11+, FastAPI, SQLite (stdlib `sqlite3`), Pydantic v2, pytest.

## Global Constraints

- Daemon-global, not org-scoped (direct-connect authority already enforces this).
- Never persist, log, return, or make queryable a raw `hrreg_...` token (already enforced upstream; the coordinator never touches token plaintext).
- A daemon-owned wrapper remains hash-pinned; a same-path child content/version upgrade is allowed and MUST be durably audited as `dependency_updated` (out of scope for first commit of Slice 1 — dependency upgrade audit is a Slice-1 follow-up task, tracked at the end of this plan; initial registration is in scope now).
- The operation must be idempotent and crash-safe: a durable `COMMITTED` record is the authority for exact-once completion; in-memory registry state is never authoritative on its own.
- Compensate failures in a deterministic, durable order; leave no reusable authority or partial YAML/registry/adapter/profile state.
- No new subprocess launch beyond the ALREADY-existing conformance-probe mechanism (`run_conformance_probe`) — this slice does not add a second Popen path, and it never launches a real agentic task.
- `docs/agent-guides/orchestrator-contracts.md` and `docs/agent-guides/features-and-invariants.md` are NOT read yet by this plan's author for Slice 1 — re-check before Slice 2/3 if profile/registry contracts described there diverge from what's found in code.

## Key Design Decisions (stated explicitly — confirm before large-scale execution)

1. **Where projection is invoked — REVISED after Task 3 hit a pinned invariant:** the original design (call the coordinator synchronously inside `connect()`) was implemented and then reverted — `tests/daemon/test_direct_connect_ingress.py::test_valid_direct_ingress_writes_exactly_one_nonlaunchable_receipt` monkeypatches `subprocess.Popen` globally and asserts **zero calls** happen during `/connect`, and the module's own docstring states the route "remains a receipt-only, no-process boundary." The coordinator's conformance probe (Task 2) spawns exactly one subprocess, so it cannot run inside that request. Confirmed with the user: projection is triggered by a **new follow-up route**, `POST /api/v1/runtime/custom-cli/{operation_id}/commit` — master-bearer-authed (`dependencies=[require_token()]`, mirroring `/runtime/adapters/{adapter_id}/approve`), NOT registration-token-authed (the registration token was already consumed by `/connect`). The Settings/onboarding UI (Slice 3) calls `/connect` then immediately `/commit`, so Connect→Connected is still one perceived user action across two fast sequential requests. `/connect`'s response and pinned tests are UNCHANGED — no new fields.
2. **Where `version` / `capabilities` / `contract_version` come from**, since `DirectManifestV2` (merged, immutable) carries none of them:
   - `contract_version` and `version` are read from the wrapper's own conformance-probe `AdapterOutput.adapter_metadata` (`contract_version`, `adapter_version`) — the SAME mechanism the legacy master-bearer adapter-registration path already uses to validate a wrapper speaks the adapter contract.
   - `capabilities` defaults to `[]` (baseline-only posture; there is no manual-entry UI step in the direct-connect flow to declare capabilities, and D5 baseline-only posture forbids inventing permission/capability expansion here).
3. **Adapter goes straight to `status="approved"`**, skipping the legacy `"pending"` state entirely. The connect-time route already performed the equivalent of founder trust (hash-pinned wrapper at a daemon-issued path, symlink-free, structurally verified) — that IS the D4 approval gate's evidentiary basis, just established cryptographically/structurally instead of by a manual founder click. `registered_by` / `approved_by` are both recorded as the literal string `"direct-connect"` so the audit trail is honest about provenance.
4. **`_perform_adapter_profile_binding` is reused as-is** (unchanged) for the profile-write + registry-replace + audit step. It already implements exactly the "write durable profile → replace in-memory registry → audit, with compensating rollback on any failure" behavior Slice 1 needs. The coordinator's own compensation only has to cover the ONE step that function doesn't: deleting the `AdapterEntry` this coordinator just created in `adapter_store` if binding fails.

---

## File Structure

- **Modify:** `runtime/daemon/direct_connect_store.py` — add `direct_connect_projections` table + `plan_projection` / `mark_committed` / `mark_failed` / `get_projection` / `get_receipt_for_projection` methods. Additive only; no existing table/column changes.
- **Create:** `runtime/daemon/direct_connect_projection.py` — the coordinator: `project(store, operation_id, *, now=None) -> ProjectionOutcome`.
- **Modify:** `runtime/daemon/routes/direct_connect.py` — call the coordinator after the existing receive/commit sequence; extend (not replace) the JSON response.
- **Test:** `tests/daemon/test_direct_connect_projection.py` — coordinator unit tests (fault injection, idempotency, concurrency, compensation).
- **Test:** `tests/daemon/test_direct_connect_ingress.py` — extend with end-to-end `/connect` → COMMITTED assertions (existing file, additive tests only, no existing test bodies changed).

---

## Task 1: Durable projection state in `DirectConnectAuthorityStore`

**Files:**
- Modify: `runtime/daemon/direct_connect_store.py`
- Test: `tests/daemon/test_direct_connect_authority.py`

**Interfaces:**
- Produces: `DirectConnectAuthorityStore.plan_projection(operation_id: str, *, now: float | None = None) -> bool` — inserts a `planned` row; returns `False` (no-op) if a row already exists for this `operation_id` (idempotent retry signal).
- Produces: `DirectConnectAuthorityStore.mark_committed(operation_id: str, *, adapter_id: str, profile_name: str, now: float | None = None) -> bool` — transitions `planned → committed`; returns `False` if the row isn't in `planned` state.
- Produces: `DirectConnectAuthorityStore.mark_failed(operation_id: str, reason: str, *, now: float | None = None) -> bool` — transitions `planned → failed`; returns `False` if not in `planned` state.
- Produces: `DirectConnectAuthorityStore.get_projection(operation_id: str) -> DirectConnectProjection | None` — read-only lookup.
- Produces: `DirectConnectAuthorityStore.get_receipt_artifacts(operation_id: str) -> DirectConnectReceiptArtifacts | None` — reads back the immutable wrapper + children artifacts recorded by the (already-merged) `receive()` call, keyed by `operation_id`. Returns `None` if no receipt exists.
- Consumes: existing `direct_connect_operations`, `direct_connect_artifacts`, `direct_connect_receipts` tables (read-only for this task).

- [ ] **Step 1: Write failing tests for the new schema + state machine**

```python
# tests/daemon/test_direct_connect_authority.py (append)

def test_plan_projection_is_idempotent_per_operation(tmp_path) -> None:
    from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore

    store = DirectConnectAuthorityStore(tmp_path / "direct.db", runtime_root=tmp_path)
    store.mint_authority(
        token_plaintext="hrreg_plan", name="custom-cli", intended_profile_name="profile",
        workspace_adapter_id="codex", issued_at=1, expires_at=100,
    )
    operation_id = store.reserve("hrreg_plan", now=2)
    store.receive(
        "hrreg_plan", operation_id, wrapper_sha256="a" * 64,
        wrapper_facts={}, children=[], now=2,
    )

    assert store.plan_projection(operation_id, now=3) is True
    assert store.plan_projection(operation_id, now=4) is False  # already planned
    projection = store.get_projection(operation_id)
    assert projection.state == "planned"


def test_mark_committed_requires_planned_state(tmp_path) -> None:
    from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore

    store = DirectConnectAuthorityStore(tmp_path / "direct.db", runtime_root=tmp_path)
    assert store.mark_committed("unknown-op", adapter_id="a", profile_name="p") is False

    store.mint_authority(
        token_plaintext="hrreg_commit", name="custom-cli", intended_profile_name="profile",
        workspace_adapter_id="codex", issued_at=1, expires_at=100,
    )
    operation_id = store.reserve("hrreg_commit", now=2)
    store.receive("hrreg_commit", operation_id, wrapper_sha256="b" * 64, wrapper_facts={}, children=[], now=2)
    store.plan_projection(operation_id, now=3)

    assert store.mark_committed(operation_id, adapter_id="custom-cli-adapter", profile_name="profile", now=4) is True
    projection = store.get_projection(operation_id)
    assert projection.state == "committed"
    assert projection.adapter_id == "custom-cli-adapter"
    # Retrying commit on an already-committed row is a no-op, not an error
    assert store.mark_committed(operation_id, adapter_id="custom-cli-adapter", profile_name="profile", now=5) is False


def test_mark_failed_from_planned_and_reopen_durability(tmp_path) -> None:
    from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore

    path = tmp_path / "direct.db"
    store = DirectConnectAuthorityStore(path, runtime_root=tmp_path)
    store.mint_authority(
        token_plaintext="hrreg_fail", name="custom-cli", intended_profile_name="profile",
        workspace_adapter_id="codex", issued_at=1, expires_at=100,
    )
    operation_id = store.reserve("hrreg_fail", now=2)
    store.receive("hrreg_fail", operation_id, wrapper_sha256="c" * 64, wrapper_facts={}, children=[], now=2)
    store.plan_projection(operation_id, now=3)
    assert store.mark_failed(operation_id, "conformance_probe_failed", now=4) is True
    store.close()

    reopened = DirectConnectAuthorityStore(path, runtime_root=tmp_path)
    projection = reopened.get_projection(operation_id)
    assert projection.state == "failed"
    assert projection.reason == "conformance_probe_failed"


def test_get_receipt_artifacts_returns_wrapper_and_children(tmp_path) -> None:
    from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore

    store = DirectConnectAuthorityStore(tmp_path / "direct.db", runtime_root=tmp_path)
    store.mint_authority(
        token_plaintext="hrreg_art", name="custom-cli", intended_profile_name="profile",
        workspace_adapter_id="codex", issued_at=1, expires_at=100,
    )
    operation_id = store.reserve("hrreg_art", now=2)
    store.receive(
        "hrreg_art", operation_id, wrapper_sha256="d" * 64, wrapper_facts={"mode": 493},
        children=[{"slot": "cli", "path": "/abs/child", "sha256": "e" * 64, "facts": {"version_probe_argv": ["/abs/child", "--version"]}}],
        now=2,
    )

    artifacts = store.get_receipt_artifacts(operation_id)
    assert artifacts.wrapper_path.name  # non-empty Path
    assert artifacts.wrapper_sha256 == "d" * 64
    assert artifacts.children == [{"slot": "cli", "executable": "/abs/child", "sha256": "e" * 64}]
    assert artifacts.intended_profile_name == "profile"
    assert artifacts.workspace_adapter_id == "codex"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/daemon/test_direct_connect_authority.py -k "projection or receipt_artifacts" -v`
Expected: FAIL — `AttributeError: 'DirectConnectAuthorityStore' object has no attribute 'plan_projection'` (and similar for the other new methods).

- [ ] **Step 3: Implement the schema + methods**

In `runtime/daemon/direct_connect_store.py`, add to `_init_schema` (after the existing `direct_connect_events` table):

```python
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS direct_connect_projections (
                operation_id TEXT PRIMARY KEY,
                token_fingerprint TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('planned', 'committed', 'failed')),
                adapter_id TEXT,
                profile_name TEXT,
                reason TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )"""
        )
```

Add the dataclasses near the top (after `DirectConnectReceipt`):

```python
@dataclass(frozen=True)
class DirectConnectProjection:
    operation_id: str
    token_fingerprint: str
    state: str
    adapter_id: str | None
    profile_name: str | None
    reason: str | None


@dataclass(frozen=True)
class DirectConnectReceiptArtifacts:
    operation_id: str
    wrapper_path: Path
    wrapper_sha256: str
    children: list[dict[str, str]]
    intended_profile_name: str
    workspace_adapter_id: str
```

Add the methods (near `compensate_received`, before `counts`):

```python
    def get_receipt_artifacts(self, operation_id: str) -> DirectConnectReceiptArtifacts | None:
        with self._lock:
            cursor = self._conn.cursor()
            operation = cursor.execute(
                """SELECT token_fingerprint, intended_profile_name, workspace_adapter_id
                   FROM direct_connect_operations WHERE operation_id = ?""",
                (operation_id,),
            ).fetchone()
            if operation is None:
                return None
            authority = self._read_authority(cursor, operation["token_fingerprint"])
            if authority is None:
                return None
            rows = cursor.execute(
                """SELECT slot, kind, declared_path, sha256 FROM direct_connect_artifacts
                   WHERE operation_id = ? ORDER BY slot""",
                (operation_id,),
            ).fetchall()
            wrapper_sha256 = ""
            children: list[dict[str, str]] = []
            for row in rows:
                if row["kind"] == "immutable_wrapper":
                    wrapper_sha256 = row["sha256"]
                else:
                    children.append({
                        "slot": row["slot"], "executable": row["declared_path"], "sha256": row["sha256"],
                    })
            return DirectConnectReceiptArtifacts(
                operation_id=operation_id,
                wrapper_path=authority.wrapper_destination,
                wrapper_sha256=wrapper_sha256,
                children=children,
                intended_profile_name=operation["intended_profile_name"],
                workspace_adapter_id=operation["workspace_adapter_id"],
            )

    def plan_projection(self, operation_id: str, *, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        with self._lock, self._conn:
            cursor = self._conn.cursor()
            operation = cursor.execute(
                "SELECT token_fingerprint FROM direct_connect_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if operation is None:
                raise RuntimeError("cannot plan projection for an unreceived operation")
            try:
                cursor.execute(
                    """INSERT INTO direct_connect_projections
                       (operation_id, token_fingerprint, state, adapter_id, profile_name, reason, created_at, updated_at)
                       VALUES (?, ?, 'planned', NULL, NULL, NULL, ?, ?)""",
                    (operation_id, operation["token_fingerprint"], now, now),
                )
            except sqlite3.IntegrityError:
                return False
            return True

    def _read_projection(self, cursor: sqlite3.Cursor, operation_id: str) -> DirectConnectProjection | None:
        row = cursor.execute(
            """SELECT operation_id, token_fingerprint, state, adapter_id, profile_name, reason
               FROM direct_connect_projections WHERE operation_id = ?""",
            (operation_id,),
        ).fetchone()
        if row is None:
            return None
        return DirectConnectProjection(
            operation_id=row["operation_id"], token_fingerprint=row["token_fingerprint"],
            state=row["state"], adapter_id=row["adapter_id"], profile_name=row["profile_name"],
            reason=row["reason"],
        )

    def get_projection(self, operation_id: str) -> DirectConnectProjection | None:
        with self._lock:
            return self._read_projection(self._conn.cursor(), operation_id)

    def mark_committed(
        self, operation_id: str, *, adapter_id: str, profile_name: str, now: float | None = None
    ) -> bool:
        now = time.time() if now is None else now
        with self._lock, self._conn:
            cursor = self._conn.cursor()
            updated = cursor.execute(
                """UPDATE direct_connect_projections
                   SET state = 'committed', adapter_id = ?, profile_name = ?, updated_at = ?
                   WHERE operation_id = ? AND state = 'planned'""",
                (adapter_id, profile_name, now, operation_id),
            ).rowcount
            if updated:
                cursor.execute(
                    """INSERT INTO direct_connect_events
                       (event_id, operation_id, token_fingerprint, event_type, detail, created_at)
                       SELECT ?, operation_id, token_fingerprint, 'committed', ?, ?
                       FROM direct_connect_projections WHERE operation_id = ?""",
                    (str(uuid.uuid4()), f"adapter={adapter_id} profile={profile_name}", now, operation_id),
                )
            return bool(updated)

    def mark_failed(self, operation_id: str, reason: str, *, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        with self._lock, self._conn:
            cursor = self._conn.cursor()
            updated = cursor.execute(
                """UPDATE direct_connect_projections
                   SET state = 'failed', reason = ?, updated_at = ?
                   WHERE operation_id = ? AND state = 'planned'""",
                (reason, now, operation_id),
            ).rowcount
            if updated:
                cursor.execute(
                    """INSERT INTO direct_connect_events
                       (event_id, operation_id, token_fingerprint, event_type, detail, created_at)
                       SELECT ?, operation_id, token_fingerprint, 'projection_failed', ?, ?
                       FROM direct_connect_projections WHERE operation_id = ?""",
                    (str(uuid.uuid4()), reason, now, operation_id),
                )
            return bool(updated)
```

`Path` and `dataclass` are already imported at the top of the file; no new imports needed for this task.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/daemon/test_direct_connect_authority.py -v`
Expected: PASS — all existing tests still pass, all new tests pass.

- [ ] **Step 5: Commit**

```bash
git add runtime/daemon/direct_connect_store.py tests/daemon/test_direct_connect_authority.py
git commit -m "feat(daemon): add durable projection state to direct-connect authority store"
```

---

## Task 2: Projection coordinator

**Files:**
- Create: `runtime/daemon/direct_connect_projection.py`
- Test: `tests/daemon/test_direct_connect_projection.py`

**Interfaces:**
- Consumes: `DirectConnectAuthorityStore` methods from Task 1 (`get_receipt_artifacts`, `plan_projection`, `mark_committed`, `mark_failed`, `get_projection`).
- Consumes: `runtime.orchestrator.custom_adapter_registry.generate_adapter_id`, `run_conformance_probe`, `_perform_adapter_profile_binding` (module-private but same-package; this is an internal daemon↔orchestrator seam, matching how `direct_connect.py` already imports store internals).
- Consumes: `runtime.orchestrator.adapter_store.AdapterEntry`, `save_adapter`, `get_adapter`, `remove_adapter`, `acquire_store_lock`, `release_store_lock`.
- Produces: `ProjectionOutcome` dataclass — `{state: Literal["committed", "failed"], adapter_id: str | None, profile_name: str | None, reason: str | None}`.
- Produces: `project(store: DirectConnectAuthorityStore, operation_id: str, *, now: float | None = None) -> ProjectionOutcome` — the coordinator entry point.

- [ ] **Step 1: Write failing tests**

```python
# tests/daemon/test_direct_connect_projection.py
"""THR-107 Slice 1: direct-connect projection coordinator tests."""
from __future__ import annotations

import hashlib
import json
import subprocess

import pytest


def _mint_and_receive(store, tmp_path, *, token="hrreg_proj", profile_name="custom-profile", adapter="codex"):
    store.mint_authority(
        token_plaintext=token, name="custom-cli", intended_profile_name=profile_name,
        workspace_adapter_id=adapter, issued_at=1, expires_at=1000,
    )
    authority = store.get_for_token(token)
    wrapper = authority.wrapper_destination
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_bytes(b"#!/bin/sh\ncat\n")
    wrapper.chmod(0o700)
    wrapper_hash = hashlib.sha256(wrapper.read_bytes()).hexdigest()
    child = tmp_path / "bin" / "child"
    child.parent.mkdir(parents=True, exist_ok=True)
    child.write_bytes(b"#!/bin/sh\nexit 0\n")
    child.chmod(0o700)
    child_hash = hashlib.sha256(child.read_bytes()).hexdigest()
    operation_id = store.reserve(token, now=2)
    store.receive(
        token, operation_id, wrapper_sha256=wrapper_hash, wrapper_facts={},
        children=[{"slot": "cli", "path": str(child), "sha256": child_hash, "facts": {}}],
        now=2,
    )
    return operation_id, wrapper


def _fake_probe_output(adapter_id: str) -> bytes:
    payload = {
        "success": True, "duration_seconds": 0, "session_id": "probe-sess-00000000-0000-0000-0000-000000000000",
        "returncode": 0, "stdout_tail": "", "stderr_tail": "",
        "adapter_metadata": {"adapter": adapter_id, "adapter_version": "1.2.3", "contract_version": 1},
    }
    return json.dumps(payload).encode()


@pytest.fixture
def store(tmp_path, monkeypatch):
    from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore

    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / "daemon"))
    s = DirectConnectAuthorityStore(tmp_path / "direct.db", runtime_root=tmp_path)
    yield s
    s.close()


@pytest.fixture(autouse=True)
def reset_registry():
    from runtime.orchestrator.executor_registry import reset_registry as _reset
    _reset()
    yield
    _reset()


def test_successful_projection_commits_adapter_and_profile(store, tmp_path, monkeypatch):
    from runtime.daemon.direct_connect_projection import project
    from runtime.orchestrator import custom_adapter_registry
    from runtime.orchestrator.executor_registry import get_registry

    operation_id, wrapper = _mint_and_receive(store, tmp_path)

    def fake_popen(args, **kwargs):
        class _P:
            returncode = 0
            stdin = _Stdin()
            def wait(self, timeout=None): return 0
        return _P()

    class _Stdin:
        def write(self, data): pass
        def close(self): pass

    adapter_id = custom_adapter_registry.generate_adapter_id("custom-profile-adapter")
    monkeypatch.setattr(
        custom_adapter_registry, "run_conformance_probe",
        lambda executable, name: _parse(_fake_probe_output(name)),
    )

    outcome = project(store, operation_id)

    assert outcome.state == "committed"
    assert outcome.adapter_id == adapter_id
    assert outcome.profile_name == "custom-profile"
    from runtime.orchestrator.adapter_store import get_adapter
    entry = get_adapter(adapter_id)
    assert entry.status == "approved"
    assert entry.registered_by == entry.approved_by == "direct-connect"
    assert entry.dependency_manifest_version == 1
    assert len(entry.dependencies) == 1
    profile = get_registry().get_profile("custom-profile")
    assert profile is not None
    assert profile.command_adapter_id == f"custom-adapter:{adapter_id}"
    projection = store.get_projection(operation_id)
    assert projection.state == "committed"


def _parse(raw: bytes):
    from runtime.orchestrator.adapter_contract import AdapterOutput
    return AdapterOutput.model_validate(json.loads(raw))


def test_projection_is_idempotent_on_retry(store, tmp_path, monkeypatch):
    from runtime.daemon.direct_connect_projection import project
    from runtime.orchestrator import custom_adapter_registry

    operation_id, wrapper = _mint_and_receive(store, tmp_path)
    monkeypatch.setattr(
        custom_adapter_registry, "run_conformance_probe",
        lambda executable, name: _parse(_fake_probe_output(name)),
    )

    first = project(store, operation_id)
    second = project(store, operation_id)

    assert first.state == second.state == "committed"
    assert first.adapter_id == second.adapter_id


def test_conformance_probe_failure_compensates_with_no_partial_state(store, tmp_path, monkeypatch):
    from runtime.daemon.direct_connect_projection import project
    from runtime.orchestrator import custom_adapter_registry
    from runtime.orchestrator.adapter_store import load_adapters
    from runtime.orchestrator.executor_registry import get_registry

    operation_id, wrapper = _mint_and_receive(store, tmp_path)
    monkeypatch.setattr(
        custom_adapter_registry, "run_conformance_probe",
        lambda executable, name: (_ for _ in ()).throw(ValueError("probe failed")),
    )

    outcome = project(store, operation_id)

    assert outcome.state == "failed"
    assert load_adapters() == {}
    assert get_registry().get_profile("custom-profile") is None
    projection = store.get_projection(operation_id)
    assert projection.state == "failed"
    assert projection.reason


def test_profile_binding_failure_removes_adapter_entry(store, tmp_path, monkeypatch):
    from runtime.daemon.direct_connect_projection import project
    from runtime.orchestrator import custom_adapter_registry
    from runtime.orchestrator.adapter_store import load_adapters

    operation_id, wrapper = _mint_and_receive(store, tmp_path)
    monkeypatch.setattr(
        custom_adapter_registry, "run_conformance_probe",
        lambda executable, name: _parse(_fake_probe_output(name)),
    )
    monkeypatch.setattr(
        custom_adapter_registry, "_perform_adapter_profile_binding",
        lambda **kwargs: (_ for _ in ()).throw(ValueError("binding failed")),
    )

    outcome = project(store, operation_id)

    assert outcome.state == "failed"
    assert load_adapters() == {}


def test_unknown_operation_raises(store):
    from runtime.daemon.direct_connect_projection import project

    with pytest.raises(RuntimeError, match="no receipt"):
        project(store, "does-not-exist")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/daemon/test_direct_connect_projection.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'runtime.daemon.direct_connect_projection'`

- [ ] **Step 3: Implement the coordinator**

```python
# runtime/daemon/direct_connect_projection.py
"""THR-107 Slice 1: direct-connect projection coordinator.

Turns a durable, non-launchable direct-connect receipt into a durably
COMMITTED, launch-eligible custom-adapter executor profile. Reuses the
existing custom-adapter persistence primitives (adapter_store,
custom_adapter_registry._perform_adapter_profile_binding) rather than
inventing a second profile/registry write path — a direct-connect
adapter and a legacy-approved adapter are indistinguishable to the
launch fence (build_executor / resolve_adapter) once this coordinator
durably commits them.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore


@dataclass(frozen=True)
class ProjectionOutcome:
    state: Literal["committed", "failed"]
    adapter_id: str | None
    profile_name: str | None
    reason: str | None


def project(
    store: DirectConnectAuthorityStore, operation_id: str, *, now: float | None = None
) -> ProjectionOutcome:
    """Drive one direct-connect receipt to COMMITTED, or fail closed.

    Idempotent: if this operation is already committed, returns the
    existing outcome without redoing any work. Every failure path
    compensates so no partial adapter/profile/registry state survives.
    """
    from runtime.orchestrator import custom_adapter_registry
    from runtime.orchestrator.adapter_store import (
        AdapterEntry,
        acquire_store_lock,
        get_adapter,
        release_store_lock,
        save_adapter,
    )

    existing = store.get_projection(operation_id)
    if existing is not None and existing.state == "committed":
        return ProjectionOutcome(
            state="committed", adapter_id=existing.adapter_id,
            profile_name=existing.profile_name, reason=None,
        )
    if existing is not None and existing.state == "failed":
        return ProjectionOutcome(
            state="failed", adapter_id=None, profile_name=None, reason=existing.reason,
        )

    artifacts = store.get_receipt_artifacts(operation_id)
    if artifacts is None:
        raise RuntimeError(f"no receipt found for direct-connect operation {operation_id!r}")

    if existing is None:
        store.plan_projection(operation_id, now=now)

    adapter_id = custom_adapter_registry.generate_adapter_id(
        f"{artifacts.intended_profile_name}-adapter"
    )

    try:
        probe_output = custom_adapter_registry.run_conformance_probe(
            str(artifacts.wrapper_path), adapter_id
        )
    except Exception as exc:
        store.mark_failed(operation_id, f"conformance_probe_failed: {exc}", now=now)
        return ProjectionOutcome(state="failed", adapter_id=None, profile_name=None, reason=str(exc))

    entry = AdapterEntry(
        id=adapter_id,
        name=artifacts.intended_profile_name,
        executable=str(artifacts.wrapper_path),
        executable_hash=artifacts.wrapper_sha256,
        version=probe_output.adapter_metadata.adapter_version,
        capabilities=[],
        contract_version=probe_output.adapter_metadata.contract_version,
        workspace_adapter=artifacts.workspace_adapter_id,
        status="approved",
        registered_at=datetime.now(timezone.utc).isoformat(),
        registered_by="direct-connect",
        approved_at=datetime.now(timezone.utc).isoformat(),
        approved_by="direct-connect",
        intended_profile_name=artifacts.intended_profile_name,
        dependency_manifest_version=1,
        dependencies=[{"executable": c["executable"], "sha256": c["sha256"]} for c in artifacts.children],
    )

    acquire_store_lock()
    adapter_persisted = False
    try:
        if get_adapter(adapter_id) is None:
            save_adapter(entry)
            adapter_persisted = True
    finally:
        release_store_lock()

    try:
        bind_result = custom_adapter_registry._perform_adapter_profile_binding(
            adapter_id=adapter_id,
            profile_name=artifacts.intended_profile_name,
            workspace_adapter=artifacts.workspace_adapter_id,
        )
    except Exception as exc:
        if adapter_persisted:
            from runtime.orchestrator.adapter_store import remove_adapter
            remove_adapter(adapter_id)
        store.mark_failed(operation_id, f"profile_binding_failed: {exc}", now=now)
        return ProjectionOutcome(state="failed", adapter_id=None, profile_name=None, reason=str(exc))

    store.mark_committed(
        operation_id, adapter_id=adapter_id, profile_name=bind_result["profile_name"], now=now,
    )
    return ProjectionOutcome(
        state="committed", adapter_id=adapter_id, profile_name=bind_result["profile_name"], reason=None,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/daemon/test_direct_connect_projection.py -v`
Expected: PASS — all 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add runtime/daemon/direct_connect_projection.py tests/daemon/test_direct_connect_projection.py
git commit -m "feat(daemon): add direct-connect projection coordinator (THR-107 slice 1)"
```

---

## Task 3: Wire the coordinator into the connect route

**Files:**
- Modify: `runtime/daemon/routes/direct_connect.py`
- Test: `tests/daemon/test_direct_connect_ingress.py`

**Interfaces:**
- Consumes: `runtime.daemon.direct_connect_projection.project`.
- Produces: extended JSON response from `POST /runtime/custom-cli/connect` — adds `"profile_state"` (`"committed"` or `"failed"`) and, when committed, `"profile_name"`. Existing `"operation_id"` and `"state"` keys are unchanged.

- [ ] **Step 1: Write failing test**

```python
# tests/daemon/test_direct_connect_ingress.py (append)

def test_valid_direct_ingress_projects_to_committed_profile(client, tmp_path, monkeypatch):
    tc, state = client
    token = _mint(tc)
    authority = state.direct_connect_authority_store.get_for_token(token)
    wrapper_hash = _write_executable(authority.wrapper_destination, b"#!/bin/sh\ncat\n")
    child = tmp_path / "bin" / "child"
    _write_executable(child, b"#!/bin/sh\necho 1\n")

    from runtime.orchestrator import custom_adapter_registry
    from runtime.orchestrator.adapter_contract import AdapterOutput

    def fake_probe(executable, adapter_id):
        return AdapterOutput.model_validate({
            "success": True, "duration_seconds": 0,
            "session_id": "probe-sess-00000000-0000-0000-0000-000000000000",
            "returncode": 0, "stdout_tail": "", "stderr_tail": "",
            "adapter_metadata": {"adapter": adapter_id, "adapter_version": "9.9.9", "contract_version": 1},
        })

    monkeypatch.setattr(custom_adapter_registry, "run_conformance_probe", fake_probe)

    response = tc.post(
        "/api/v1/runtime/custom-cli/connect", json=_payload(wrapper_hash, child),
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["state"] == "received_nonlaunchable"  # existing contract preserved
    assert body["profile_state"] == "committed"
    assert body["profile_name"] == "custom-profile"

    from runtime.orchestrator.executor_registry import get_registry
    assert get_registry().get_profile("custom-profile") is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/daemon/test_direct_connect_ingress.py -k projects_to_committed -v`
Expected: FAIL — `KeyError: 'profile_state'`

- [ ] **Step 3: Wire the coordinator into the route**

In `runtime/daemon/routes/direct_connect.py`, add the import at the top:

```python
from runtime.daemon.direct_connect_projection import project as project_direct_connect
```

Replace the final two lines of `connect()`:

```python
    if not token_store.commit_runtime(token):
        authority_store.compensate_received(token, operation_id, "registration_token_commit_failed", now=now)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="direct intake receipt unavailable")
    return {"operation_id": receipt.operation_id, "state": receipt.state}
```

with:

```python
    if not token_store.commit_runtime(token):
        authority_store.compensate_received(token, operation_id, "registration_token_commit_failed", now=now)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="direct intake receipt unavailable")
    outcome = project_direct_connect(authority_store, operation_id, now=now)
    result = {"operation_id": receipt.operation_id, "state": receipt.state, "profile_state": outcome.state}
    if outcome.state == "committed":
        result["profile_name"] = outcome.profile_name
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/daemon/test_direct_connect_ingress.py -v`
Expected: PASS — all existing tests (unchanged assertions) still pass, new test passes.

- [ ] **Step 5: Run the full direct-connect + projection suite together**

Run: `uv run python -m pytest tests/daemon/test_direct_connect_authority.py tests/daemon/test_direct_connect_ingress.py tests/daemon/test_direct_connect_projection.py -v`
Expected: PASS — 0 failures.

- [ ] **Step 6: Commit**

```bash
git add runtime/daemon/routes/direct_connect.py tests/daemon/test_direct_connect_ingress.py
git commit -m "feat(daemon): synchronously project direct-connect receipts to Connected (THR-107 slice 1)"
```

---

## Task 4: Concurrency, replay, and reopen-durability proof

**Files:**
- Test: `tests/daemon/test_direct_connect_projection.py` (append)

**Interfaces:**
- Consumes: everything from Tasks 1–2. No new production code expected; if a race is found, fix it in `direct_connect_store.py`'s new methods (SQLite transactions already serialize via `self._lock` + `with self._conn`, so this task is expected to be a proof step, not a feature step).

- [ ] **Step 1: Write the concurrency + reopen test**

```python
def test_concurrent_projection_has_one_committer(store, tmp_path, monkeypatch):
    import threading
    from runtime.daemon.direct_connect_projection import project
    from runtime.orchestrator import custom_adapter_registry

    operation_id, wrapper = _mint_and_receive(store, tmp_path)
    monkeypatch.setattr(
        custom_adapter_registry, "run_conformance_probe",
        lambda executable, name: _parse(_fake_probe_output(name)),
    )
    barrier = threading.Barrier(2)
    outcomes = []

    def run():
        barrier.wait()
        outcomes.append(project(store, operation_id))

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(o.state == "committed" for o in outcomes)
    assert len({o.adapter_id for o in outcomes}) == 1


def test_projection_state_survives_store_reopen(tmp_path, monkeypatch):
    from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore
    from runtime.daemon.direct_connect_projection import project
    from runtime.orchestrator import custom_adapter_registry

    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / "daemon"))
    path = tmp_path / "direct.db"
    store = DirectConnectAuthorityStore(path, runtime_root=tmp_path)
    operation_id, wrapper = _mint_and_receive(store, tmp_path, token="hrreg_reopen")
    monkeypatch.setattr(
        custom_adapter_registry, "run_conformance_probe",
        lambda executable, name: _parse(_fake_probe_output(name)),
    )
    committed = project(store, operation_id)
    store.close()

    reopened = DirectConnectAuthorityStore(path, runtime_root=tmp_path)
    projection = reopened.get_projection(operation_id)
    assert projection.state == "committed"
    assert projection.adapter_id == committed.adapter_id
    reopened.close()
```

- [ ] **Step 2: Run and confirm pass (or fix a discovered race)**

Run: `uv run python -m pytest tests/daemon/test_direct_connect_projection.py -v`
Expected: PASS. If `test_concurrent_projection_has_one_committer` reveals two committers, the fix is to make `plan_projection`'s `INSERT` (already `UNIQUE` via `PRIMARY KEY`) the sole arbiter — the second thread's `plan_projection` call returns `False`, so it MUST skip straight to polling `get_projection` for the winner's result rather than re-running the probe. Update `project()` accordingly: when `plan_projection` returns `False` and no existing terminal state exists yet, loop with a short bound (e.g., re-check `get_projection` up to 50 times with a 20ms sleep) until the concurrent winner reaches `committed`/`failed`, then return that outcome instead of proceeding.

- [ ] **Step 3: Commit**

```bash
git add tests/daemon/test_direct_connect_projection.py runtime/daemon/direct_connect_projection.py
git commit -m "test(daemon): prove direct-connect projection concurrency and reopen durability"
```

---

## Self-Review Notes (already applied above)

- **Spec coverage:** validates wrapper+dependency facts (Task 2, conformance probe + hash reuse from the immutable receipt) — durable plan/receipt before token consumption (token already consumed by merged Slice-1A before this coordinator runs; plan row created immediately after, same request) — projects only after durable commit (Task 2's `mark_committed` happens last) — idempotent retries (Task 2 idempotency test) — deterministic compensation (Task 2 failure tests) — fault injection for plan/projection/audit (Task 2) — concurrency/reopen (Task 4). **Not yet covered by this plan:** replay-after-expiry and foreign-token cases (these are Slice-1A/authority-store concerns already covered by `test_direct_connect_authority.py`'s existing reservation tests — re-verify during Task 4 review rather than duplicating); `dependency_updated` audit for same-path child upgrades (explicitly deferred — add as Task 5 once Slice 1 lands, see below); "no raw token in OpenAPI" (already proven for the mint route by the existing `test_runtime_mint_openapi_exposes_optional_direct_workspace_adapter` test; the `/connect` route takes no token in its request BODY schema — only in the `Authorization` header, which FastAPI/OpenAPI never schemas — so no new OpenAPI test is needed for Slice 1).
- **Placeholder scan:** none found — every step has real, runnable code.
- **Type consistency:** `ProjectionOutcome.state` / `DirectConnectProjection.state` use the same three-value vocabulary (`planned`/`committed`/`failed`) consistently across store and coordinator.

## Deferred to a Task 5 (after Slice 1 lands, before Slice 2 starts)

- `dependency_updated` audit event for a same-path child content/version upgrade on re-connect of an already-COMMITTED profile (the ratified constraint's upgrade path — out of scope for the FIRST commit of a given profile, which is all Task 1–4 cover).
- Slice 2 (launch fence) and Slice 3 (UI cutover) get their own plan documents once Slice 1 is reviewed, per the restart protocol's "split at the durable-state boundary" instruction — early investigation (this session) found that `build_executor()` / `resolve_adapter()` / `CustomAdapterExecutor` ALREADY gate every launch on `AdapterEntry.status == "approved"` + on-disk hash re-verification, for every custom-adapter profile regardless of origin. That means Slice 2 is likely mostly a **proof** task (write the 5 shipping-path tests the handoff doc demands) rather than new gating code — but this must be verified by reading `runtime/daemon/thread_runner.py`, `wake_runner.py`, `dream_runner.py`, `schedule_runner.py`, and `runtime/orchestrator/orchestrator.py`'s task-launch path in full before assuming no new code is needed.
