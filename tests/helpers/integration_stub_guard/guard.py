"""Test-only exact executable/registry/plan/callback identity admission."""
from __future__ import annotations

import hashlib
import functools
import json
import os
from pathlib import Path
import stat
import sys

PROVIDERS = {"claude", "codex", "opencode"}


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            info.st_uid, info.st_mode, info.st_nlink)


def _read_owned(path: Path, cap: int = 65536, *, executable: bool = False) -> bytes:
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
            or before.st_nlink != 1 or before.st_size > cap
            or (executable and not os.access(path, os.X_OK))):
        raise RuntimeError("test_identity_refused")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(fd)
        data = os.read(fd, cap + 1)
        if (_identity(opened) != _identity(before) or _identity(os.fstat(fd)) != _identity(opened) or _identity(path.lstat()) != _identity(opened)
                or len(data) != opened.st_size):
            raise RuntimeError("test_identity_changed")
        return data
    finally:
        os.close(fd)


def manifest() -> dict:
    return json.loads(_read_owned(Path(os.environ["HAPPYRANCH_TEST_PARENT_MANIFEST"])))


def require_parent_environment() -> None:
    binding = manifest()
    root = Path(binding["root"])
    expected = {"HOME": root / "home", "XDG_CONFIG_HOME": root / "config",
                "XDG_CACHE_HOME": root / "cache"}
    if any(os.environ.get(key) != str(path) for key, path in expected.items()):
        raise RuntimeError("test_parent_environment_unexpected")
    if os.environ.get("PATH") != os.pathsep.join((str(root / "bin"), "/usr/bin", "/bin")):
        raise RuntimeError("test_parent_path_unexpected")
    if any(key in os.environ for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CODEX_HOME", "CLAUDE_CONFIG_DIR",
                                         "HAPPYRANCH_RUNTIME", "HAPPYRANCH_DAEMON_TOKEN", "HAPPYRANCH_ORG_SLUG")):
        raise RuntimeError("test_parent_state_unexpected")


def validate_stub(provider: str, path: str, expected: dict | None = None) -> str:
    binding = manifest() if expected is None else expected
    if provider not in PROVIDERS or provider not in binding["stubs"]:
        raise RuntimeError("test_provider_unavailable")
    item = binding["stubs"][provider]
    if path != item["path"]:
        raise RuntimeError("test_executable_unexpected")
    data = _read_owned(Path(path), executable=True)
    if hashlib.sha256(data).hexdigest() != item["sha256"]:
        raise RuntimeError("test_executable_changed")
    return path


def validate_registry(entries: dict, expected: dict | None = None) -> None:
    binding = manifest() if expected is None else expected
    if set(entries) != set(binding["stubs"]):
        raise RuntimeError("test_registry_unexpected")
    for provider, path in entries.items():
        validate_stub(provider, path, binding)


def validate_plan(path: str) -> None:
    if not path:
        raise RuntimeError("test_plan_missing")
    data = _read_owned(Path(path), executable=True)
    expected = _read_owned(Path(path + ".sha256"), cap=64).decode("ascii")
    if hashlib.sha256(data).hexdigest() != expected:
        raise RuntimeError("test_plan_changed")


def validate_callback(binding: dict | None = None) -> None:
    binding = manifest() if binding is None else binding
    expected = binding["callback"]
    # Admit precisely the first executable callback on the effective PATH.
    found = next((str(Path(part) / "happyranch") for part in os.environ["PATH"].split(os.pathsep)
                  if os.access(Path(part) / "happyranch", os.X_OK)), None)
    if found != expected or hashlib.sha256(_read_owned(Path(expected), executable=True)).hexdigest() != binding["callback_sha256"]:
        raise RuntimeError("test_callback_unexpected")


def witness(row: dict) -> None:
    data = (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
    directory = Path(os.environ["HAPPYRANCH_TEST_WITNESS_DIR"])
    if len(data) > 1024 or directory.is_symlink() or directory.stat().st_uid != os.getuid():
        raise RuntimeError("test_witness_refused")
    fd = os.open(directory / "identities.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_size + len(data) > 65536:
            raise RuntimeError("test_witness_refused")
        if os.write(fd, data) != len(data):
            raise RuntimeError("test_witness_short")
    finally:
        os.close(fd)


def install() -> None:
    if "HAPPYRANCH_TEST_PARENT_MANIFEST" not in os.environ:
        return
    binding = manifest()
    source = Path(binding["source"])
    import runtime.orchestrator.executors as executors
    if Path(executors.__file__).resolve() != source / "runtime/orchestrator/executors.py":
        raise RuntimeError("test_runtime_source_unexpected")
    original = executors._resolve_binary
    if getattr(original, "_test_stub_guard", False):
        return
    @functools.wraps(original)
    def resolve(provider):
        from runtime.orchestrator.executor_binary_registry import load_registry
        # Real registry resolution remains the first boundary, including stale/missing refusal.
        path = original(provider)
        validate_registry(load_registry())
        return validate_stub(provider, path)
    resolve._test_stub_guard = True
    executors._resolve_binary = resolve
    def unavailable(*args, **kwargs):
        raise RuntimeError("test_custom_provider_unavailable")
    executors.CustomAdapterExecutor.run = unavailable
    executors.CustomAdapterExecutor.build_launch_spec = unavailable


if __name__ == "__main__":
    # Called by exact-source shell stubs immediately before the explicit plan.
    try:
        stub, provider, plan, *argv = sys.argv[1:]
        binding = manifest()
        validate_stub(provider, stub, binding)
        home = Path(os.environ["HAPPYRANCH_DAEMON_HOME"])
        validate_registry(json.loads(_read_owned(home / "executors.json")), binding)
        validate_plan(plan)
        validate_callback(binding)
        witness({"kind": "stub", "provider": provider, "stub_sha256": binding["stubs"][provider]["sha256"],
                 "source_sha": binding["revision"], "plan_sha256": hashlib.sha256(_read_owned(Path(plan))).hexdigest(),
                 "argc": len(argv), "flags": [arg for arg in argv if arg in
                    {"-p", "--json", "run", "--format", "--output-format", "--resume", "-s", "-"}]})
    except (KeyError, ValueError, OSError, RuntimeError):
        raise SystemExit("deterministic stub setup refused")


def assert_launch_witness(provider: str, *, callbacks: int = 1) -> None:
    binding = manifest()
    path = Path(os.environ["HAPPYRANCH_TEST_WITNESS_DIR"]) / "identities.jsonl"
    rows = [json.loads(line) for line in _read_owned(path).splitlines()]
    stubs = [row for row in rows if row.get("kind") == "stub" and row.get("provider") == provider]
    calls = [row for row in rows if row.get("kind") == "callback"]
    assert stubs, "actual deterministic executable launch witness missing"
    assert all(row["source_sha"] == binding["revision"] and row["stub_sha256"] == binding["stubs"][provider]["sha256"] for row in stubs)
    assert all("-p" in row["flags"] if provider == "claude" else "--json" in row["flags"] for row in stubs)
    expected_cli = hashlib.sha256((Path(binding["source"]) / "cli/main.py").read_bytes()).hexdigest()
    assert len(calls) >= callbacks and all(row["source_sha"] == binding["revision"] and row["cli_sha256"] == expected_cli for row in calls)
