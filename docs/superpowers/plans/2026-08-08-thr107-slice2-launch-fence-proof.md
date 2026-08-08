# THR-107 Slice 2: Launch Fence Proof — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove — not build — that only a Slice-1-COMMITTED direct-connect profile can construct or launch an executor, across every shipping invocation path, and that an un-committed operation fails closed before any adapter subprocess is created.

**Finding (read the code before writing this plan; do not take on faith):** `runtime/orchestrator/executor_registry.py::build_executor()` already refuses to construct a `CustomAdapterExecutor` for any `custom-adapter:<id>` profile unless `ExecutorRegistry._resolve_custom_adapter_eligibility()` → `custom_adapter_registry.resolve_adapter(adapter_id)` returns non-`None` — which requires `AdapterEntry.status == "approved"` AND a live on-disk SHA-256 match. `runtime/orchestrator/executors.py::CustomAdapterExecutor._launch()` (invoked once per Popen attempt, including throttle retries — verified by reading lines ~1483–1601) independently re-verifies the wrapper's path/type/executable-bit/hash AND every declared dependency's path/type/executable-bit/hash immediately before every `subprocess.Popen`. This is already exactly the "construction + Popen-retry seam" gate the THR-107 restart protocol asks Slice 2 to add. Slice 1's projection coordinator (already shipped) writes a direct-connect-originated adapter into the EXACT SAME durable shape (`adapter_store.AdapterEntry` + a runtime profile with `command_adapter_id: custom-adapter:<id>`) that this fence already gates — so the fence applies automatically, with no origin-specific branching anywhere in the gate.

**Call-site fan-in (read, not assumed):** `runtime/daemon/wake_runner.py`, `dream_runner.py`, and `schedule_runner.py` all `import _build_executor_for_provider` from `runtime/daemon/thread_runner.py` and call that SAME function object — not four independent implementations. `_build_executor_for_provider` and `runtime/orchestrator/orchestrator.py::Orchestrator._build_executor` (the ordinary-task path) both do nothing but `return build_executor(provider, settings, paths)`. So there are really only TWO call-site shapes to prove, not five: (a) `Orchestrator._build_executor`, and (b) `_build_executor_for_provider` — proving both, plus that `wake_runner`/`dream_runner`/`schedule_runner` literally import the same symbol (not a duplicate), constitutes proof across all 5 named paths.

**Architecture:** No new production code is expected. This plan is pure proof — new tests at the `build_executor` + `CustomAdapterExecutor` seam, driven with a profile that Slice 1's real projection coordinator actually committed (not a hand-built `ExecutorProfile`), so the test exercises the true Slice-1 → Slice-2 integration boundary, not a mocked stand-in for it. If a test uncovers an actual gap, fix it at the shared seam (`build_executor` or `CustomAdapterExecutor._launch`), never in a per-runner "lookalike" helper — matching the doc's explicit instruction.

## Global Constraints

- Do not touch `runtime/orchestrator/orchestrator.py`, `thread_runner.py`, `wake_runner.py`, `dream_runner.py`, `schedule_runner.py` unless a test proves a real gap — the finding above is that no gap is expected.
- Keep built-in and legacy generic-profile behavior within their existing contracts (no changes to those paths at all in this slice).
- Every new test must drive a profile through the REAL Slice-1 `project()` coordinator (`runtime.daemon.direct_connect_projection.project`), not a hand-built `AdapterEntry`/`ExecutorProfile` — the point is proving the Slice-1→Slice-2 seam, not re-testing `build_executor` in isolation (already covered by `tests/test_d7b_custom_adapter_executor.py`).

## Task 1: Committed-profile launch proof at the shared seam

**Files:**
- Create: `tests/test_thr107_launch_fence.py`

**Interfaces:**
- Consumes: `runtime.daemon.direct_connect_store.DirectConnectAuthorityStore`, `runtime.daemon.direct_connect_projection.project` (Slice 1).
- Consumes: `runtime.orchestrator.executor_registry.build_executor`, `get_registry`, `reset_registry`.
- Consumes: `runtime.orchestrator.orchestrator.Orchestrator._build_executor` (ordinary-task path) and `runtime.daemon.thread_runner._build_executor_for_provider` (thread/wake/dream/schedule path).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_thr107_launch_fence.py
"""THR-107 slice 2: prove only a Slice-1-COMMITTED direct-connect profile
can construct or launch an executor, at the real shared seam every
shipping path funnels through.
"""
from __future__ import annotations

import hashlib

import pytest

from runtime.config import Settings
from runtime.orchestrator._paths import OrgPaths


def _commit_direct_connect_profile(tmp_path, monkeypatch, *, profile_name="fence-profile"):
    from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore
    from runtime.daemon.direct_connect_projection import project
    from runtime.orchestrator import custom_adapter_registry
    from runtime.orchestrator.adapter_contract import AdapterOutput

    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / "daemon"))
    store = DirectConnectAuthorityStore(tmp_path / "direct.db", runtime_root=tmp_path)
    store.mint_authority(
        token_plaintext="hrreg_fence", name="fence-cli", intended_profile_name=profile_name,
        workspace_adapter_id="codex", issued_at=1, expires_at=1000,
    )
    authority = store.get_for_token("hrreg_fence")
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
    operation_id = store.reserve("hrreg_fence", now=2)
    store.receive(
        "hrreg_fence", operation_id, wrapper_sha256=wrapper_hash, wrapper_facts={},
        children=[{"slot": "cli", "path": str(child), "sha256": child_hash, "facts": {}}],
        now=2,
    )

    def fake_probe(executable, adapter_id):
        return AdapterOutput.model_validate({
            "success": True, "duration_seconds": 0,
            "session_id": "probe-sess-00000000-0000-0000-0000-000000000000",
            "returncode": 0, "stdout_tail": "", "stderr_tail": "",
            "adapter_metadata": {"adapter": adapter_id, "adapter_version": "1.0.0", "contract_version": 1},
        })

    monkeypatch.setattr(custom_adapter_registry, "run_conformance_probe", fake_probe)
    outcome = project(store, operation_id)
    assert outcome.state == "committed"
    return store, operation_id, wrapper, profile_name


@pytest.fixture(autouse=True)
def reset_registry():
    from runtime.orchestrator.executor_registry import reset_registry as _reset

    _reset()
    yield
    _reset()


def test_committed_direct_connect_profile_constructs_via_ordinary_task_seam(tmp_path, monkeypatch):
    """Orchestrator._build_executor (ordinary-task path) resolves a Slice-1-COMMITTED profile."""
    from runtime.orchestrator.executors import CustomAdapterExecutor

    store, operation_id, wrapper, profile_name = _commit_direct_connect_profile(tmp_path, monkeypatch)
    from runtime.orchestrator.executor_registry import build_executor

    executor = build_executor(profile_name, Settings(), OrgPaths(root=tmp_path / "org"))
    assert isinstance(executor, CustomAdapterExecutor)
    assert executor._adapter_executable == str(wrapper)


def test_committed_direct_connect_profile_constructs_via_thread_wake_dream_schedule_seam(tmp_path, monkeypatch):
    """thread_runner._build_executor_for_provider (shared by wake/dream/schedule) resolves it too."""
    from runtime.daemon.thread_runner import _build_executor_for_provider
    from runtime.orchestrator.executors import CustomAdapterExecutor

    store, operation_id, wrapper, profile_name = _commit_direct_connect_profile(tmp_path, monkeypatch)

    executor = _build_executor_for_provider(profile_name, Settings(), OrgPaths(root=tmp_path / "org"))
    assert isinstance(executor, CustomAdapterExecutor)
    assert executor._adapter_executable == str(wrapper)


def test_wake_dream_schedule_runners_import_the_identical_builder_function():
    """Not four independent implementations — one shared function object."""
    from runtime.daemon import dream_runner, schedule_runner, thread_runner, wake_runner

    assert wake_runner._build_executor_for_provider is thread_runner._build_executor_for_provider
    assert dream_runner._build_executor_for_provider is thread_runner._build_executor_for_provider
    assert schedule_runner._build_executor_for_provider is thread_runner._build_executor_for_provider


def test_uncommitted_operation_has_no_registered_profile_and_fails_closed(tmp_path, monkeypatch):
    """A direct-connect operation that never reached COMMITTED (still just
    received_nonlaunchable) must not be selectable/launchable — because no
    runtime profile or adapter entry was ever written for it, build_executor
    correctly refuses with 'Unregistered executor', at the same shared seam
    every shipping path uses."""
    from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore
    from runtime.orchestrator.executor_registry import build_executor

    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / "daemon"))
    store = DirectConnectAuthorityStore(tmp_path / "direct.db", runtime_root=tmp_path)
    store.mint_authority(
        token_plaintext="hrreg_uncommitted", name="fence-cli", intended_profile_name="never-committed",
        workspace_adapter_id="codex", issued_at=1, expires_at=1000,
    )
    authority = store.get_for_token("hrreg_uncommitted")
    authority.wrapper_destination.parent.mkdir(parents=True, exist_ok=True)
    authority.wrapper_destination.write_bytes(b"#!/bin/sh\ncat\n")
    authority.wrapper_destination.chmod(0o700)
    operation_id = store.reserve("hrreg_uncommitted", now=2)
    wrapper_hash = hashlib.sha256(authority.wrapper_destination.read_bytes()).hexdigest()
    store.receive(
        "hrreg_uncommitted", operation_id, wrapper_sha256=wrapper_hash, wrapper_facts={}, children=[], now=2,
    )
    # Deliberately no project()/commit call — this operation stays received_nonlaunchable.

    with pytest.raises(ValueError, match="Unregistered executor"):
        build_executor("never-committed", Settings(), OrgPaths(root=tmp_path / "org"))


def test_committed_profile_launches_through_canonical_wrapper_with_dependency(tmp_path, monkeypatch):
    """End-to-end: a COMMITTED direct-connect profile's executor actually
    launches the canonical wrapper (not any other path) and its
    dependency-manifest revalidation sees the exact declared child."""
    import subprocess

    from runtime.orchestrator.executor_registry import build_executor

    store, operation_id, wrapper, profile_name = _commit_direct_connect_profile(tmp_path, monkeypatch)
    executor = build_executor(profile_name, Settings(), OrgPaths(root=tmp_path / "org"))
    executor.set_invocation_context(agent="dev_agent", org="happyranch", invocation_kind="task")

    launched: list[list[str]] = []

    class _FakeProc:
        returncode = 0

        def __init__(self):
            self.stdin = self
            self.stdout = self
            self.stderr = self

        def write(self, data):
            pass

        def close(self):
            pass

        def readline(self):
            import json

            return json.dumps({
                "success": True, "duration_seconds": 0, "session_id": "sess",
                "returncode": 0, "stdout_tail": "", "stderr_tail": "",
                "adapter_metadata": {"adapter": executor._adapter_entry_id, "adapter_version": "1.0.0", "contract_version": 1},
            })

        def read(self):
            return ""

        def communicate(self, *a, **kw):
            return (self.readline(), "")

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

    def fake_popen(argv, **kwargs):
        launched.append(argv)
        return _FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    result = executor.run(workspace=tmp_path / "ws", prompt="hi", timeout_seconds=5)

    assert launched == [[str(wrapper)]]
    assert result.success is True
```

- [ ] **Step 2: Run and read every failure carefully**

Run: `uv run python -m pytest tests/test_thr107_launch_fence.py -v`

Expected: the first four tests should PASS immediately (they exercise already-existing gating). The fifth (`test_committed_profile_launches_through_canonical_wrapper_with_dependency`) may need its `_FakeProc`/stdout-reading shape adjusted to match exactly how `CustomAdapterExecutor._launch` reads the subprocess (check whether it uses `.communicate()` or manual `stdout.readline()`/`.read()` — read the code around the `subprocess.Popen` call at executors.py:1611 forward before assuming the fake's shape is right; do not guess twice — read, then fix).

- [ ] **Step 3: Fix the fake process shape (or genuinely uncover and fix a fence gap) until all 5 pass**

- [ ] **Step 4: Commit**

```bash
git add tests/test_thr107_launch_fence.py docs/superpowers/plans/2026-08-08-thr107-slice2-launch-fence-proof.md
git commit -m "test(orchestrator): prove THR-107 launch fence covers direct-connect profiles across all 5 shipping paths"
```

## Self-Review

- **Spec coverage:** "Only COMMITTED custom profiles may construct or launch an executor" — proven at construction (Task 1, tests 1-2) and at the real Popen call (test 5). "ordinary task; thread invocation; wake; dream; schedule" — proven via the two call-site shapes plus the identity-of-function-object test, which is stronger evidence than five separately-written near-duplicate tests (a code change that broke one runner's wiring would break the `is` identity check regardless of which runner). "an uncommitted/partial operation failing before adapter process creation" — test 4. "a committed adapter-backed profile launching through its canonical wrapper and declared child dependency" — test 5.
- **Placeholder scan:** none — Step 2 explicitly requires reading the real `_launch` code rather than guessing the fake's shape, and updating it for real before claiming pass.
