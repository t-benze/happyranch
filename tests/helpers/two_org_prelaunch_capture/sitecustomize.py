"""Opt-in, transparent pre-session exception observation for ONE two-org case.

Never stringify an exception, inspect locals, replace inputs, or repair setup.
"""
from __future__ import annotations

import functools
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import time

MODULE_SYMBOLS = {
    "runtime.orchestrator.orchestrator": (
        "Orchestrator._run_agent", "Orchestrator._run_agent_impl",
        "Orchestrator._resolve_executor_name", "Orchestrator._build_executor",
    ),
    "runtime.orchestrator.workspace_adapters": (
        "materialize_workspace_skills", "materialize_workspace_skills_union",
        "_materialize_context_union", "_materialize_unified_canonical",
        "ensure_system_contracts_materialized", "validate_workspace_skills_integrity",
    ),
    "runtime.orchestrator.executors": ("_resolve_binary",),
    # Inspected pre-session policy closure; only these original code objects.
    "runtime.orchestrator.active_authority_policy": (
        "assert_no_reserved_team_policy_header", "resolve_policy_manager_team",
        "resolve_active_team_policy_snapshot", "persist_session_policy_binding",
        "render_selected_team_policy", "render_active_team_policy",
        "render_active_team_policy_v2", "_legacy_binding_payload", "_selector_binding_payload",
    ),
    "runtime.orchestrator.authority_policy_store": (
        "AuthorityPolicyStore.get_authority_selector", "AuthorityPolicyStore.get_activation",
        "AuthorityPolicyStore.get_release", "AuthorityPolicyStore.get_v2_activation",
        "AuthorityPolicyStore.get_v2_release",
    ),
    "runtime.infrastructure.db.authority_policy": (
        "AuthorityPolicyMixin.get_authority_selector",
        "AuthorityPolicyMixin._get_authority_selector_uncommitted",
        "AuthorityPolicyMixin._load_authority_selector_history_chain",
        "AuthorityPolicyMixin._authority_policy_selector_from_row",
        "AuthorityPolicyMixin._authenticate_authority_selector_control_audit",
        "AuthorityPolicyMixin._validate_authority_selector_team",
        "AuthorityPolicyMixin.get_authority_policy_activation",
        "AuthorityPolicyMixin.get_authority_policy_release",
        "AuthorityPolicyMixin.get_authority_policy_v2_activation",
        "AuthorityPolicyMixin.get_authority_policy_v2_release",
        "AuthorityPolicyMixin._authority_policy_activation_from_row",
        "AuthorityPolicyMixin._authority_policy_release_from_row",
        "AuthorityPolicyMixin._authority_policy_v2_activation_from_row",
        "AuthorityPolicyMixin._authority_policy_v2_release_from_row",
        "AuthorityPolicyMixin.bind_authority_policy_legacy_session",
        "AuthorityPolicyMixin.bind_authority_policy_v2_session",
    ),
}
EXCEPTION_NAMES = frozenset({"unknown", "builtins.RuntimeError", "builtins.ValueError",
                            "builtins.OSError", "builtins.TimeoutError",
                            "runtime.orchestrator.active_authority_policy.ActiveAuthorityPolicyError",
                            "runtime.orchestrator.orchestrator.WorkspaceNotInitialized",
                            "runtime.orchestrator.orchestrator.AgentUnavailableError",
                            "runtime.orchestrator.executors.ExecutorBinaryBlocked",
                            "runtime.orchestrator.workspace_adapters.WorkspaceIntegrityError",
                            "runtime.orchestrator.workspace_adapters.SystemContractMaterializationError"})
SYMBOL_NAMES = frozenset({"unknown"} | {m + "." + s for m, symbols in MODULE_SYMBOLS.items() for s in symbols})
KEYS = frozenset({"version", "source_sha", "source_digest", "org", "task_id", "category",
                  "exception", "symbol", "code"})


def validate_record(row, *, org, revision, source_digest):
    if (not isinstance(row, dict) or set(row) != KEYS or type(row["version"]) is not int
            or row["version"] != 1 or org not in {"alpha", "beta"} or row["org"] != org
            or row["task_id"] != "TASK-001" or row["category"] != "prelaunch_exception"
            or row["source_sha"] != revision or re.fullmatch(r"[a-f0-9]{40}", revision) is None
            or row["source_digest"] != source_digest or re.fullmatch(r"[a-f0-9]{64}", source_digest) is None
            or row["exception"] not in EXCEPTION_NAMES or row["symbol"] not in SYMBOL_NAMES
            or row["code"] != "unknown"):
        raise ValueError("capture_record_refused")
    return row


def source_binding(source: Path, revision: str, *, deadline: float):
    hashes = {}
    files = {name: name.replace(".", "/") + ".py" for name in MODULE_SYMBOLS}
    files.update({"runtime.daemon": "runtime/daemon/__init__.py",
                  "runtime.daemon.__main__": "runtime/daemon/__main__.py"})
    for name, relative in files.items():
        path = source / relative
        if time.monotonic() >= deadline or path.is_symlink():
            raise ValueError("capture_source_unavailable")
        before = path.stat()
        with path.open("rb") as stream:
            data = stream.read(2 * 1024 * 1024 + 1)
            after = os.fstat(stream.fileno())
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns
        ) or path.stat().st_mtime_ns != after.st_mtime_ns:
            raise ValueError("capture_source_changed")
        if len(data) > 2 * 1024 * 1024 or time.monotonic() >= deadline:
            raise ValueError("capture_source_unavailable")
        hashes[name] = hashlib.sha256(data).hexdigest()
    digest = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    return {"source": str(source), "revision": revision, "hashes": hashes, "digest": digest}


def transparent(original, capture):
    @functools.wraps(original)
    def observed(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        except BaseException as exc:
            try:
                capture(exc, args, kwargs)
            except BaseException:
                # The original identity and traceback always win over observation.
                pass
            raise
    return observed


def _write_record(directory: Path, row, *, deadline: float | None = None):
    if deadline is not None and time.monotonic() >= deadline:
        raise ValueError("capture_deadline")
    data = (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
    if len(data) > 1024:
        raise ValueError("capture_record_cap")
    # Private literal ancestry, no symlink following even on intermediate parents.
    parent = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in directory.parts[1:]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = child
        info = os.fstat(parent)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError("capture_directory_refused")
        temporary = f".{row['org']}.pending"
        final = row["org"] + "-TASK-001.json"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if deadline is not None and time.monotonic() >= deadline:
                raise ValueError("capture_deadline")
            # link is atomic and refuses an existing record; it never overwrites it.
            os.link(temporary, final, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
        finally:
            os.unlink(temporary, dir_fd=parent)
    finally:
        os.close(parent)


def _install_capture():
    if "HAPPYRANCH_TWO_ORG_CAPTURE" not in os.environ:
        return
    original_argv = sys.orig_argv
    if "-m" not in original_argv or original_argv[original_argv.index("-m") + 1:] != ["runtime.daemon"]:
        return
    from guard import _read_owned
    binding = json.loads(_read_owned(Path(os.environ["HAPPYRANCH_TWO_ORG_CAPTURE"]), cap=8192))
    source = Path(binding["source"])
    fresh = source_binding(source, binding["revision"], deadline=time.monotonic() + 1)
    if fresh != binding:
        return
    for name, relative in (("runtime.daemon", "runtime/daemon/__init__.py"),
                           ("runtime.daemon.__main__", "runtime/daemon/__main__.py")):
        spec = importlib.util.find_spec(name)
        if spec is None or Path(spec.origin).resolve() != source / relative:
            return
    codes, classes = {}, {RuntimeError: "builtins.RuntimeError", ValueError: "builtins.ValueError",
                          OSError: "builtins.OSError", TimeoutError: "builtins.TimeoutError"}
    modules = {}
    for module_name, symbols in MODULE_SYMBOLS.items():
        module = importlib.import_module(module_name)
        if Path(module.__file__).resolve() != source / (module_name.replace(".", "/") + ".py"):
            return
        modules[module_name] = module
        for symbol in symbols:
            value = module
            for part in symbol.split("."):
                value = getattr(value, part)
            while hasattr(value, "__wrapped__"):
                value = value.__wrapped__
            if (hasattr(value, "__code__") and value.__code__.co_qualname == symbol
                    and Path(value.__code__.co_filename).resolve() == Path(module.__file__).resolve()):
                codes[value.__code__] = module_name + "." + symbol
    for name in EXCEPTION_NAMES - {"unknown"}:
        module_name, _, symbol = name.rpartition(".")
        module = modules.get(module_name)
        if module is not None:
            value = getattr(module, symbol)
            if (isinstance(value, type) and issubclass(value, BaseException)
                    and value.__module__ == module_name and value.__qualname__ == symbol):
                classes[value] = name
    orchestrator = modules["runtime.orchestrator.orchestrator"].Orchestrator
    original = orchestrator._run_agent
    seen = set()
    directory = Path(os.environ["HAPPYRANCH_TWO_ORG_CAPTURE"]).parent
    def capture(exc, args, kwargs):
        # Do not inspect prompt/notes/locals. Only the explicit invocation identity.
        owner = args[0]
        task_id = args[1] if len(args) > 1 else kwargs.get("task_id")
        org = owner._slug
        if org not in {"alpha", "beta"} or task_id != "TASK-001" or org in seen:
            return
        seen.add(org)
        deadline = time.monotonic() + 0.1
        db = owner._db
        if not db._lock.acquire(timeout=0.01):
            return
        try:
            # Read the actual writer seam; any prior session_start excludes capture.
            if db._conn.execute("SELECT 1 FROM audit_log WHERE task_id=? AND action='session_start' LIMIT 1",
                                (task_id,)).fetchone() is not None:
                return
        finally:
            db._lock.release()
        if source_binding(source, binding["revision"], deadline=deadline) != binding:
            return
        frames = []
        tb = exc.__traceback__
        while tb is not None and len(frames) < 32 and time.monotonic() < deadline:
            frames.append(tb.tb_frame.f_code)
            tb = tb.tb_next
        # An unknown innermost frame is UNKNOWN, never a guessed outer owner.
        symbol = codes.get(frames[-1], "unknown") if frames and tb is None else "unknown"
        row = {"version": 1, "source_sha": binding["revision"], "source_digest": binding["digest"],
               "org": org, "task_id": task_id, "category": "prelaunch_exception",
               "exception": classes.get(type(exc), "unknown"), "symbol": symbol, "code": "unknown"}
        validate_record(row, org=org, revision=binding["revision"], source_digest=binding["digest"])
        if time.monotonic() >= deadline:
            return
        _write_record(directory, row, deadline=deadline)
    orchestrator._run_agent = transparent(original, capture)


if __name__ == "sitecustomize" and "PYTEST_CURRENT_TEST" in os.environ and "HAPPYRANCH_TEST_PARENT_MANIFEST" not in os.environ:
    os.write(2, b"integration test parent identity missing\n")
    os._exit(86)

if __name__ == "sitecustomize" and "HAPPYRANCH_TEST_PARENT_MANIFEST" in os.environ:
    try:
        from guard import install
        install()
    except Exception:
        os.write(2, b"integration stub identity unavailable\n")
        os._exit(86)
    try:
        _install_capture()
    except Exception:
        # Observational failure remains unavailable; original daemon behavior continues.
        pass
