"""Disposable test-parent bootstrap. Run BEFORE importing pytest or runtime.

Only the ordinary pytest selection is accepted. This is not a proof driver.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import stat
import sqlite3
import subprocess
import sys
import tempfile

PROVIDERS = ("claude", "codex", "opencode")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def callback_script(source: Path, python: Path, expected_cli: str) -> str:
    # Resolve cli.main from this tested source even after executor cwd changes.
    code = ("import hashlib,pathlib,runpy,sys; "
            f"s=pathlib.Path({str(source)!r}); "
            f"assert hashlib.sha256((s/'cli/main.py').read_bytes()).hexdigest()=={expected_cli!r}; "
            "sys.path.insert(0,str(s)); "
            "import cli.main as c; assert pathlib.Path(c.__file__).resolve()==s/'cli/main.py'; "
            "from guard import manifest,witness; b=manifest(); "
            "witness({'kind':'callback','source_sha':b['revision'],'cli_sha256':hashlib.sha256((s/'cli/main.py').read_bytes()).hexdigest()}); "
            "sys.argv[0]='happyranch'; c.main()")
    return "#!/bin/sh\nexec " + shlex.quote(str(python)) + " -c " + shlex.quote(code) + ' "$@"\n'


def build_environment(root: Path, source: Path, python: Path, uv: Path,
                      revision: str, *, real_platform: bool = False) -> dict[str, str]:
    """Closed environment; ambient credentials/config/registry/PATH never copied."""
    root.chmod(0o700)
    source = source.resolve()
    bin_dir = root / "bin"
    bin_dir.mkdir()
    for name in ("home", "config", "cache", "tmp", "daemon"):
        (root / name).mkdir(mode=0o700)
    for name in ("uv",):
        (bin_dir / name).symlink_to(uv)
    for name in ("python", "python3"):
        shim = bin_dir / name
        shim.write_text("#!/bin/sh\nexec " + shlex.quote(str(python)) + ' "$@"\n')
        shim.chmod(0o700)
    callback = bin_dir / "happyranch"
    callback.write_text(callback_script(source, python, digest(source / "cli/main.py")))
    callback.chmod(0o700)
    stubs = {}
    for provider in PROVIDERS:
        stub = bin_dir / provider
        stub.write_bytes((source / f"tests/integration/fake_{provider}.sh").read_bytes())
        stub.chmod(0o700)
        stubs[provider] = {"path": str(stub), "sha256": digest(stub)}
    manifest = {"source": str(source), "revision": revision, "python": str(python),
                "callback": str(callback), "callback_sha256": digest(callback),
                "stubs": stubs, "root": str(root)}
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True))
    manifest_path.chmod(0o600)
    (root / "daemon/executors.json").write_text(json.dumps({p: v["path"] for p, v in stubs.items()}))
    guard = source / "tests/helpers/integration_stub_guard"
    env = {
        "HOME": str(root / "home"), "XDG_CONFIG_HOME": str(root / "config"),
        "XDG_CACHE_HOME": str(root / "cache"), "TMPDIR": str(root / "tmp"),
        "TMP": str(root / "tmp"), "TEMP": str(root / "tmp"),
        "PATH": os.pathsep.join((str(bin_dir), "/usr/bin", "/bin")),
        "PYTHONPATH": os.pathsep.join((str(guard), str(source))),
        "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
        "HAPPYRANCH_DAEMON_HOME": str(root / "daemon"), "HAPPYRANCH_DAEMON_PORT": "0",
        "HAPPYRANCH_TEST_PARENT_MANIFEST": str(manifest_path),
        "HAPPYRANCH_TEST_STUB_GUARD": str(guard / "guard.py"),
        "CANDIDATE_PY": str(python), "EXPECTED_SOURCE": str(source), "EXPECTED_SHA": revision,
        "UV_NO_SYNC": "1", "UV_PYTHON_DOWNLOADS": "never",
        "UV_PYTHON": str(python), "UV_NO_CONFIG": "1",
        "UV_CACHE_DIR": str(root / "cache" / "uv"),
    }
    # Preserve the interpreter's own venv, never an ambient VIRTUAL_ENV.
    # uv cannot discover a provisioned interpreter through the shell shims.
    prefix = python.parent.parent
    if (prefix / "pyvenv.cfg").is_file():
        env["VIRTUAL_ENV"] = env["UV_PROJECT_ENVIRONMENT"] = str(prefix)
    if real_platform:
        env["HAPPYRANCH_TEST_REAL_PLATFORM"] = "1"
    # Test-only evidence descriptor; never copy ambient runner env wholesale.
    # The retirement fixture authenticates it before any elevated observer.
    if "HAPPYRANCH_TEST_NATIVE_OBSERVER_RECEIPT" in os.environ:
        receipt = json.loads(os.environ["HAPPYRANCH_TEST_NATIVE_OBSERVER_RECEIPT"])
        assert set(receipt) == {"path", "sha256"}
        receipt_path = Path(receipt["path"])
        assert receipt_path.is_absolute() and not receipt_path.is_symlink()
        assert not receipt_path.stat().st_mode & 0o022
        assert digest(receipt_path) == receipt["sha256"]
        env["HAPPYRANCH_TEST_NATIVE_OBSERVER_RECEIPT"] = json.dumps(receipt)
    return env


def verify_interpreter_binding(env: dict[str, str], python: Path, uv: Path,
                               source: Path, revision: str) -> None:
    """Observe nested uv's ordinary interpreter before pytest/product imports."""
    expected = {"executable": str(python.resolve()), "sha256": digest(python),
                "prefix": sys.prefix, "version": list(sys.version_info[:3])}
    code = ("import hashlib,json,pathlib,sys; p=pathlib.Path(sys.executable).resolve(); "
            "print(json.dumps({'executable':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),"
            "'prefix':sys.prefix,'version':list(sys.version_info[:3])},sort_keys=True))")
    result = subprocess.run([str(uv), "run", "python", "-I", "-c", code],
                            cwd=source, env=env, capture_output=True, text=True,
                            timeout=15)
    if result.returncode or len(result.stdout) > 65536:
        raise SystemExit("integration parent uv interpreter observation refused: "
                         + result.stderr[:4096])
    observed = json.loads(result.stdout)
    if observed != expected:
        raise SystemExit("integration parent uv interpreter binding differs: "
                         + json.dumps({"expected": expected, "observed": observed}, sort_keys=True))
    print(json.dumps({"kind": "integration-parent-interpreter", "source": str(source),
                      "revision": revision, "binding": observed}, sort_keys=True), flush=True)


# These selections are the TASK10394 finite hosted L/W release, not M authority.
ROSTER_E2E = "tests/integration/test_human_team_roster_e2e.py::"
ROSTER_BROWSER = "tests/integration/test_human_team_roster_browser.py::test_c10_bilingual_existing_views"
OLD_READER = "b3179b123fddbb0f0f604ed9e0d148f1b23455f3"
BROWSER_PACKAGES = {"@playwright/cli": "0.1.18", "playwright": "1.63.0-alpha-2026-08-05",
                    "playwright-core": "1.63.0-alpha-2026-08-05"}


def private_directory(path: Path) -> Path:
    if (not path.is_absolute() or path.is_symlink() or path.resolve(strict=True) != path
            or not path.is_dir() or path.stat().st_uid != os.getuid()
            or stat.S_IMODE(path.stat().st_mode) != 0o700):
        raise ValueError("roster_private_invocation_directory_required")
    return path


def roster_selection(args: list[str]) -> None:
    # Do not collect whole files, units, or M cases when exposing feature inputs.
    if args[:3] != ["pytest", "-m", "integration"]:
        raise ValueError("roster_explicit_integration_nodes_required")
    nodes = [item for item in args[3:] if item not in ("-v", "-vv", "--tb=short")]
    unrestricted = {"test_c1_registry_and_attachment", "test_c1_settings_save_and_rollback",
                    "test_c2_owner_required_before_persistence", "test_c3_worker_lifecycle_and_denials",
                    "test_c4_normal_and_recovered_verdict_attribution", "test_c7_both_resume_resets_and_worker_contexts"}
    bounded = {"test_c5_schema_history_publication_and_portability": {
        "human-current", "legacy-agent-current", "current-graph", "current-graph-explicit-profile",
        "b317-schema2-refusal", "b317-schema1-control"},
        "test_c8_preflight_refusals_and_backup_cas": {"no-inhibition", "design-plan-not-manifest", "wrong-digest"},
        "test_c9_crash_recovery_and_replay": {"missing-direction", "missing-operation", "wrong-digest", "design-plan-not-manifest"}}
    if not nodes:
        raise ValueError("roster_explicit_integration_nodes_required")
    for node in nodes:
        if node == ROSTER_BROWSER:
            continue
        if not node.startswith(ROSTER_E2E):
            raise ValueError("roster_selection_outside_released_radius")
        name, _, parameter = node[len(ROSTER_E2E):].partition("[")
        if name in unrestricted and not parameter:
            continue
        if name in bounded and parameter.endswith("]") and parameter[:-1] in bounded[name]:
            continue
        raise ValueError("roster_selection_outside_released_radius")


def roster_bindings(options, source: Path, python: Path) -> dict:
    output = private_directory(Path(options.roster_output))
    invocation = private_directory(output.parent)
    if (not invocation.name.startswith("happyranch-roster-") or output.name != "evidence"
            or invocation.is_relative_to(source) or any(output.iterdir())):
        raise ValueError("roster_empty_owned_evidence_directory_required")
    binding = {"output": str(output), "python": str(python.resolve()), "python_sha256": digest(python)}
    if options.roster_old_reader:
        old = Path(options.roster_old_reader)
        if (old != invocation / "old-reader" or old.is_symlink() or not old.is_dir()
                or old.resolve(strict=True) != old or old.stat().st_uid != os.getuid()):
            raise ValueError("roster_independent_old_reader_required")
        revision = subprocess.check_output(["git", "-C", str(old), "rev-parse", "HEAD"], text=True).strip()
        if (revision != OLD_READER or subprocess.check_output(
                ["git", "-C", str(old), "status", "--porcelain", "--untracked-files=all"])):
            raise ValueError("roster_clean_pinned_old_reader_required")
        binding["old_reader"] = {"source": str(old), "revision": revision,
            "modules": {name: digest(old / name) for name in (
                "runtime/daemon/org_state.py", "runtime/workflows/authority.py", "runtime/orchestrator/teams.py")}}
    if options.roster_browser_tools:
        descriptor = Path(options.roster_browser_tools)
        if descriptor != invocation / "browser-binding.json":
            raise ValueError("roster_owned_browser_descriptor_required")
        info = descriptor.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or info.st_mode & 0o022 or info.st_size > 65536):
            raise ValueError("roster_private_browser_descriptor_required")
        browser = json.loads(descriptor.read_text())
        required = {"node", "node_sha256", "node_version", "cli_sha256", "browser", "browser_sha256",
                    "browser_version", "browser_revision", "lock_sha256", "registry_integrities"}
        if set(browser) != required:
            raise ValueError("roster_browser_descriptor_shape")
        tool = private_directory(invocation / "tools")
        node = Path(browser["node"])
        cli = tool / "node_modules/@playwright/cli/playwright-cli.js"
        executable = Path(browser["browser"])
        for path, expected in ((node, browser["node_sha256"]), (cli, browser["cli_sha256"]),
                               (executable, browser["browser_sha256"])):
            if (not path.is_absolute() or path.is_symlink() or path.resolve(strict=True) != path
                    or not path.is_file() or path.stat().st_mode & 0o022 or digest(path) != expected):
                raise ValueError("roster_browser_file_binding_mismatch")
        if not executable.is_relative_to(invocation / "browsers"):
            raise ValueError("roster_owned_browser_required")
        observation_env = {"PATH": "/usr/bin:/bin", "HOME": str(invocation), "NO_UPDATE_NOTIFIER": "1",
                           "PLAYWRIGHT_BROWSERS_PATH": str(invocation / "browsers")}
        version = subprocess.check_output([str(node), "--version"], env=observation_env, text=True, timeout=15).strip()
        if version != browser["node_version"] or not version.startswith("v24."):
            raise ValueError("roster_node24_binding_mismatch")
        if subprocess.check_output([str(executable), "--version"], env=observation_env, text=True, timeout=15).strip() != browser["browser_version"]:
            raise ValueError("roster_browser_version_mismatch")
        lock_path = tool / "package-lock.json"
        if digest(lock_path) != browser["lock_sha256"]:
            raise ValueError("roster_tool_lock_mismatch")
        lock = json.loads(lock_path.read_text())["packages"]
        if set(browser["registry_integrities"]) != set(BROWSER_PACKAGES):
            raise ValueError("roster_registry_integrity_population_mismatch")
        for name, expected in BROWSER_PACKAGES.items():
            installed = json.loads((tool / "node_modules" / name / "package.json").read_text())
            entry = lock["node_modules/" + name]
            if (installed["version"] != expected or installed["license"] != "Apache-2.0"
                    or entry["version"] != expected or entry["integrity"] != browser["registry_integrities"][name]
                    or entry["resolved"] != "https://registry.npmjs.org/" + name + "/-/" + name.split("/")[-1] + "-" + expected + ".tgz"):
                raise ValueError("roster_pinned_browser_package_mismatch")
        core = tool / "node_modules/playwright-core"
        browsers = json.loads((core / "browsers.json").read_text())["browsers"]
        chromium = next(row for row in browsers if row["name"] == "chromium")
        selected = subprocess.check_output([str(node), "-e", "console.log(require(process.argv[1]).chromium.executablePath())", str(core)],
                                          env=observation_env, text=True, timeout=15).strip()
        if selected != str(executable) or chromium["revision"] != browser["browser_revision"]:
            raise ValueError("roster_browser_revision_mismatch")
        browser.update(cli=str(cli), tool_root=str(tool), browsers_sha256=digest(core / "browsers.json"))
        binding["browser"] = browser
    return binding


def expose_roster(root: Path, env: dict[str, str], binding: dict) -> None:
    if "old_reader" in binding:
        env["HAPPYRANCH_TEST_ROSTER_OLD_READER_SOURCE"] = binding["old_reader"]["source"]
    if "browser" in binding:
        browser = binding["browser"]
        for name, command in (("node", "exec " + shlex.quote(browser["node"])),
                ("playwright-cli", "exec " + shlex.quote(browser["node"]) + " " + shlex.quote(browser["cli"]))):
            shim = root / "bin" / name
            shim.write_text("#!/bin/sh\n" + command + ' "$@"\n')
            shim.chmod(0o700)
            browser[name + "_shim_sha256"] = digest(shim)
        config = root / "browser-config.json"
        config.write_text(json.dumps({"browser": {"browserName": "chromium", "isolated": True,
            "launchOptions": {"headless": True, "executablePath": browser["browser"]}}}))
        config.chmod(0o600)
        browser["config"] = str(config)
        browser["config_sha256"] = digest(config)
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(Path(binding["output"]).parent / "browsers")
        env["NO_UPDATE_NOTIFIER"] = "1"
    manifest_path = Path(env["HAPPYRANCH_TEST_PARENT_MANIFEST"])
    manifest = json.loads(manifest_path.read_text())
    manifest["roster"] = binding
    manifest_path.write_text(json.dumps(manifest, sort_keys=True))
    print(json.dumps({"kind": "integration-parent-roster-bindings", "binding": binding}, sort_keys=True), flush=True)


def retain_roster(root: Path, binding: dict, source: Path, revision: str, code: int) -> None:
    output = Path(binding["output"])
    inventory = []
    # Keep only owned scenario evidence, never fixture credentials/config/DBs.
    for path in sorted((root / "tmp").rglob("*")):
        if (path.is_symlink() or not path.is_file() or path.stat().st_uid != os.getuid()
                or not ((path.name.startswith("C") and path.suffix in (".json", ".png"))
                        or path.name in ("identities.jsonl", "actual-contexts.jsonl")
                        or path.name.endswith(".calls.jsonl")
                        or path.name.startswith("real-failed-cut.json"))):
            continue
        relative = path.relative_to(root / "tmp")
        target = output / "scenarios" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        inventory.append({"original_path": str(path), "path": str(target.relative_to(output)), "sha256": digest(target), "bytes": target.stat().st_size})
    if ROSTER_E2E + "test_c4_normal_and_recovered_verdict_attribution" in binding["selections"]:
        # Independent final durable readback after the actual child/fixture tail;
        # native cut witnesses above retain earlier phases. This is not PASS.
        for db in sorted((root / "tmp").rglob("happyranch.db")):
            if db.is_symlink() or db.stat().st_uid != os.getuid():
                raise ValueError("roster_c4_owned_readback_required")
            with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True) as reader:
                reader.row_factory = sqlite3.Row
                tables = {name: [dict(row) for row in reader.execute('SELECT * FROM "' + name + '" ORDER BY rowid')]
                          for name in ("tasks", "task_results", "audit_log", "task_completion_recoveries", "jobs")}
            target = output / "scenarios" / db.relative_to(root / "tmp").parent / "C4-final-durable-readback.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps({"database": str(db), "source_sha": revision, "tables": tables,
                "meaning": "actual final readback; see child exit/assertions and native witnesses"},
                sort_keys=True, default=lambda value: {"bytes_hex": value.hex()}))
            inventory.append({"original_path": str(db), "path": str(target.relative_to(output)), "sha256": digest(target), "bytes": target.stat().st_size})
    receipt = {"source": str(source), "revision": revision, "exit": code, "binding": binding, "artifacts": inventory}
    (output / "parent-receipt.json").write_text(json.dumps(receipt, sort_keys=True))
    print(json.dumps({"kind": "integration-parent-roster-artifacts", "receipt": receipt}, sort_keys=True), flush=True)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    options = None
    if args[:1] and args[0].startswith("--roster-"):
        boundary = args.index("--")
        parser = argparse.ArgumentParser()
        parser.add_argument("--roster-old-reader")
        parser.add_argument("--roster-browser-tools")
        parser.add_argument("--roster-output", required=True)
        options = parser.parse_args(args[:boundary])
        args = args[boundary:]
    if args[:1] == ["--"]:
        args.pop(0)
    if args[:1] != ["pytest"]:
        raise SystemExit("integration parent requires the ordinary pytest command")
    source = Path(__file__).resolve().parents[2]
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if subprocess.check_output(["git", "-C", str(source), "status", "--porcelain"]):
        raise SystemExit("integration parent requires a clean committed source")
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("integration parent requires installed uv")
    binding = None
    if options is not None:
        roster_selection(args)
        needs_old = any(node.startswith(ROSTER_E2E + "test_c5_schema_history_publication_and_portability[b317-") for node in args)
        if needs_old != bool(options.roster_old_reader) or (ROSTER_BROWSER in args) != bool(options.roster_browser_tools):
            raise ValueError("roster_selected_case_binding_required")
        binding = roster_bindings(options, source, Path(sys.executable))
        binding["selections"] = args[3:]
    with tempfile.TemporaryDirectory(prefix="happyranch-test-parent-") as directory:
        env = build_environment(Path(directory), source, Path(sys.executable), Path(uv).resolve(), revision,
                                real_platform=os.environ.get("HAPPYRANCH_TEST_REAL_PLATFORM") == "1")
        if binding is not None:
            expose_roster(Path(directory), env, binding)
            args.extend(["-o", "tmp_path_retention_policy=all", "--basetemp=" + str(Path(directory) / "tmp/pytest"),
                         "--junitxml=" + str(Path(binding["output"]) / "pytest.xml")])
        verify_interpreter_binding(env, Path(sys.executable), Path(uv).resolve(), source, revision)
        child = subprocess.Popen([sys.executable, "-m", "pytest", *args[1:]], env=env,
                                 cwd=source, start_new_session=True)
        previous = {}
        code = 1
        def forward(signum, frame):
            os.killpg(child.pid, signum)
        try:
            for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                previous[signum] = signal.signal(signum, forward)
            code = child.wait()
            return code if code >= 0 else 128 - code
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
            # Exact invocation-owned group; never a process census or host sweep.
            for signum in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(child.pid, signum)
                except ProcessLookupError:
                    break
            child.wait(timeout=5)
            if binding is not None:
                retain_roster(Path(directory), binding, source, revision, code if code >= 0 else 128 - code)


if __name__ == "__main__":
    raise SystemExit(main())
