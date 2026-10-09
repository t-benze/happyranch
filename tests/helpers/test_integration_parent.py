"""Pure environment/file identity checks; no provider or daemon is invoked."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

from tests.helpers.integration_parent import build_environment
from tests.helpers.integration_stub_guard.guard import (
    validate_callback, validate_plan, validate_registry, validate_stub,
)
from tests.helpers.deterministic_plan import DeterministicPlan


@pytest.fixture
def binding(tmp_path, monkeypatch):
    root = tmp_path / "parent"
    root.mkdir()
    source = Path(__file__).resolve().parents[2]
    with monkeypatch.context() as ambient:
        ambient.setenv("ANTHROPIC_API_KEY", "PLANTED_PRIVATE_TOKEN")
        ambient.setenv("HAPPYRANCH_DAEMON_HOME", "/unrelated/runtime")
        env = build_environment(root, source, Path(sys.executable), Path("/usr/bin/true"), "a" * 40)
    data = json.loads(Path(env["HAPPYRANCH_TEST_PARENT_MANIFEST"]).read_text())
    return env, data


def test_parent_environment_is_closed_before_imports(binding):
    env, data = binding
    assert "ANTHROPIC_API_KEY" not in env
    assert "PLANTED_PRIVATE_TOKEN" not in json.dumps(env)
    assert env["HAPPYRANCH_DAEMON_HOME"] != "/unrelated/runtime"
    assert env["HOME"] != os.environ["HOME"]
    assert all(Path(env[key]).is_dir() for key in ("HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "TMPDIR"))
    assert env["PATH"].split(os.pathsep)[1:] == ["/usr/bin", "/bin"]
    assert "runtime" not in sys.modules or Path(sys.modules["runtime"].__file__).resolve().parents[1] == Path(data["source"])
    assert set(data["stubs"]) == {"claude", "codex", "opencode"}


@pytest.mark.parametrize("case", ["missing", "pi", "custom", "unexpected", "stale", "nonexecutable", "changed", "extra"])
def test_registry_admission_refuses_before_execution(binding, tmp_path, case):
    _, data = binding
    entries = {p: row["path"] for p, row in data["stubs"].items()}
    validate_registry(entries, data)
    if case == "missing":
        entries.pop("claude")
    elif case in {"pi", "custom", "extra"}:
        entries[case] = entries["claude"]
    elif case == "unexpected":
        executable = tmp_path / "never-executed"
        executable.write_bytes(Path(entries["claude"]).read_bytes())
        executable.chmod(0o700)
        entries["claude"] = str(executable)
    elif case == "stale":
        Path(entries["claude"]).unlink()
    elif case == "nonexecutable":
        Path(entries["claude"]).chmod(0o600)
    else:
        Path(entries["claude"]).write_text("#!/bin/sh\nexit 0\n")
    with pytest.raises((RuntimeError, OSError)):
        validate_registry(entries, data)


def test_callback_identity_refuses_path_shadow_and_changed_bytes(binding, tmp_path, monkeypatch):
    env, data = binding
    monkeypatch.setenv("PATH", env["PATH"])
    validate_callback(data)
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    (shadow / "happyranch").write_text("#!/bin/sh\nexit 0\n")
    (shadow / "happyranch").chmod(0o700)
    monkeypatch.setenv("PATH", str(shadow) + os.pathsep + env["PATH"])
    with pytest.raises(RuntimeError, match="test_callback_unexpected"):
        validate_callback(data)
    monkeypatch.setenv("PATH", env["PATH"])
    Path(data["callback"]).write_text("#!/bin/sh\nexit 0\n")
    with pytest.raises(RuntimeError, match="test_callback_unexpected"):
        validate_callback(data)


def test_explicit_noop_plan_is_bound_and_absent_or_changed_plan_refuses(tmp_path):
    with pytest.raises(RuntimeError, match="test_plan_missing"):
        validate_plan("")
    plan = DeterministicPlan(tmp_path / "noop.sh")
    plan.write_text("#!/bin/sh\nexit 0\n")
    validate_plan(str(plan))
    # Bypass the test author's explicit approval seam to simulate stale bytes.
    Path(plan).write_text("#!/bin/sh\nexit 7\n")
    with pytest.raises(RuntimeError, match="test_plan_changed"):
        validate_plan(str(plan))


def test_parent_preserves_only_explicit_real_platform_test_switch(binding, tmp_path):
    _, data = binding
    root = tmp_path / "real-platform"
    root.mkdir()
    env = build_environment(root, Path(data["source"]), Path(sys.executable), Path("/usr/bin/true"),
                            "a" * 40, real_platform=True)
    assert env["HAPPYRANCH_TEST_REAL_PLATFORM"] == "1"
    assert "ANTHROPIC_API_KEY" not in env


@pytest.mark.parametrize("key,value", [("HOME", "/production/home"), ("PATH", "/production/bin"),
                                       ("OPENAI_API_KEY", "PLANTED_PRIVATE")])
def test_parent_admission_refuses_forged_marker_or_ambient_state(binding, monkeypatch, key, value):
    env, data = binding
    from tests.helpers.integration_stub_guard.guard import require_parent_environment
    with monkeypatch.context() as patch:
        for existing in list(os.environ):
            patch.delenv(existing)
        for name, content in env.items():
            patch.setenv(name, content)
        require_parent_environment()
        patch.setenv(key, value)
        with pytest.raises(RuntimeError):
            require_parent_environment()


@pytest.mark.parametrize("case", ["valid", "missing", "unexpected", "stale", "nonexecutable", "pi", "custom"])
def test_real_registered_resolver_has_test_identity_fence_before_launch(binding, tmp_path, monkeypatch, case):
    _, data = binding
    from tests.helpers.integration_stub_guard.guard import install
    from runtime.orchestrator import executors
    from runtime.orchestrator.executor_binary_registry import save_registry
    manifest_path = tmp_path / "binding.json"
    manifest_path.write_text(json.dumps(data))
    manifest_path.chmod(0o600)
    monkeypatch.setenv("HAPPYRANCH_TEST_PARENT_MANIFEST", str(manifest_path))
    monkeypatch.setattr(executors, "_resolve_binary", executors._resolve_binary)
    monkeypatch.setattr(executors.CustomAdapterExecutor, "run", executors.CustomAdapterExecutor.run)
    monkeypatch.setattr(executors.CustomAdapterExecutor, "build_launch_spec", executors.CustomAdapterExecutor.build_launch_spec)
    install()
    entries = {p: row["path"] for p, row in data["stubs"].items()}
    provider = "claude"
    if case == "missing":
        entries.pop("claude")
    elif case == "unexpected":
        executable = tmp_path / "never-executed"
        executable.write_bytes(Path(entries["claude"]).read_bytes())
        executable.chmod(0o700)
        entries["claude"] = str(executable)
    elif case == "stale":
        Path(entries["claude"]).unlink()
    elif case == "nonexecutable":
        Path(entries["claude"]).chmod(0o600)
    elif case == "pi":
        entries["pi"] = entries["claude"]
        provider = "pi"
    save_registry(entries)
    if case == "valid":
        assert executors._resolve_binary(provider) == data["stubs"][provider]["path"]
    elif case == "custom":
        adapter = executors.CustomAdapterExecutor.__new__(executors.CustomAdapterExecutor)
        with pytest.raises(RuntimeError, match="test_custom_provider_unavailable"):
            adapter.build_launch_spec()
    else:
        with pytest.raises((RuntimeError, executors.ExecutorBinaryBlocked, OSError)):
            executors._resolve_binary(provider)
