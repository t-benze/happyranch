from __future__ import annotations

import subprocess
import os
import json
import sys
import stat
from pathlib import Path

import pytest

from tests import thr211_containment as containment


def _pipeline_shell(name: str ="prepareShell") -> str:
    """Decode the actual Groovy single-quoted literal, including continuations."""
    import re
    text = (Path(__file__).parents[1] / "Jenkinsfile").read_text()
    body = text.split("def " + name + " = '''", 1)[1].split("'''", 1)[0]
    escapes = {"n": "\n", "r": "\r", "t": "\t", "\\": "\\", "'": "'", "\n": ""}
    return re.sub(r"\\([\s\S])", lambda m: escapes[m[1]], body)


def _controlled_prepare_shell() -> str:
    """Model an outside-UID immutable lease on non-root CI workers.

    Only native UID/host and kernel write-access checks are substituted. Refusal
    tests below separately exercise the unmodified shell against writable files.
    """
    return (_pipeline_shell().replace('$(/usr/bin/id -u)', '999')
            .replace('$(/bin/hostname)', 'controlled-host')
            .replace('! -w "$ADMITTED_READY"', '-r "$ADMITTED_READY"')
            .replace('[[ ! -w "$ancestor" ]]', '[[ -d "$ancestor" ]]'))


def _shell_exit(result: subprocess.CompletedProcess[bytes]) -> int:
    lines = result.stdout.decode().splitlines()
    assert result.returncode == 0, result.stderr
    return int(next(line.removeprefix("THR211_EXIT=") for line in lines if line.startswith("THR211_EXIT=")))


def _acquired(result: subprocess.CompletedProcess[bytes]) -> dict:
    return json.loads(next(line.removeprefix("THR211_ACQUIRED=") for line in result.stdout.decode().splitlines() if line.startswith("THR211_ACQUIRED=")))


def _pipeline_tools(tmp_path: Path, *, failure: str = "") -> tuple[dict, Path]:
    """Real inline publication code; controlled Git and checkout-Python tools."""
    import getpass
    import time
    root = tmp_path / "workspace"
    root.mkdir()
    tools = tmp_path / "tools"
    tools.mkdir()
    trace = tmp_path / "trace"
    import shlex
    git = tools / "git"
    git.write_text("#!/bin/bash -p\nset -eu\n" + f"trace={shlex.quote(str(trace))}\n" +
                   'printf "git %s\\n" "$*" >> "$trace"\n' +
                   '/usr/bin/env > "${trace}.env"\n' +
                   ("exit 37\n" if failure == "clone" else "") +
                   ("kill -TERM \"$$\"\n" if failure == "abort" else "") +
                   ("mkdir \"${TMPDIR%/tmp}/artifacts/shell-result.json\"; exit 37\n" if failure == "publication" else "") +
                   'case " $* " in\n' +
                   '  *" clone "*) mkdir "${@: -1}";;\n' +
                   '  *" rev-parse "*) printf "%s\\n" ' + ("b" * 40 if failure == "head" else "a" * 40) + ';;\n' +
                   '  *" symbolic-ref "*) exit 1;;\n' +
                   'esac\n')
    python = tools / "python"
    python.write_text("#!/bin/bash -p\nset -eu\n" + f'if [[ "${{2:-}}" == -c ]]; then exec {shlex.quote(sys.executable)} "$@"; fi\n' + f"trace={shlex.quote(str(trace))}\n" +
                      'printf "python %s\\n" "$*" >> "$trace"\n/bin/cat >> "${trace}.python"\n' +
                      ("exit 38\n" if failure == "python" else ""))
    uv = tools / "uv"
    uv.write_text("#!/bin/bash -p\nset -eu\n" + f"trace={shlex.quote(str(trace))}\n" +
                  'printf "uv %s\\n" "$*" >> "$trace"\n' +
                  ("exit 39\n" if failure == "uv" else "") +
                  'if [[ "$1" == sync ]]; then\n mkdir -p "$UV_PROJECT_ENVIRONMENT/bin"\n' +
                  f' cp {shlex.quote(str(python))} "$UV_PROJECT_ENVIRONMENT/bin/python"\nfi\n')
    for tool in (git, python, uv):
        tool.chmod(0o700)
    now = int(time.time())
    admitted = {"REQUEST": "controlled-1", "MODE": "SETUP", "SOURCE": "a" * 40,
                "PIPELINE": "b" * 64, "LOCK": "c" * 64, "CONFIG": "d" * 64,
                "NODE": "controlled-node", "ACCOUNT": getpass.getuser(), "START": str(now - 1),
                "EXPIRY": str(now + 4190), "PYTHON": str(python), "UV": str(uv), "GIT": str(git),
                "ARCH": "controlled-arch", "UID": "999", "HOST": "controlled-host", "READY": str(tools / "ready")}
    env = {"PATH": "/usr/bin:/bin", "WORKSPACE": str(root), "BUILD_NUMBER": "1", "NODE_NAME": "controlled-node", "PUBLICATION_NONCE": "controlled-nonce"}
    env.update({f"ADMITTED_{key}": value for key, value in admitted.items()})
    env.update({f"REQUESTED_{key}": admitted[key] for key in ("REQUEST", "MODE", "SOURCE", "PIPELINE")})
    env['REQUESTED_EVALUATED'] = 'e' * 64
    (tools / 'ready').write_text(' '.join([admitted['REQUEST'], admitted['SOURCE'], admitted['PIPELINE'], admitted['CONFIG'], '999', 'controlled-host', str(now + 8), 'e' * 64, 'ready']) + '\n')
    return env, trace


@pytest.mark.parametrize("mode", ["SETUP", "ABORT", "DIAGNOSTIC"])
def test_actual_pipeline_prepares_privately_for_each_admitted_mode(tmp_path, mode):
    env, trace = _pipeline_tools(tmp_path)
    env.update(ADMITTED_MODE=mode, REQUESTED_MODE=mode, HTTPS_PROXY="credential-canary",
               BASH_ENV="/no-such-startup", PYTHONPATH="credential-canary", FAKE_CLAUDE_PLAN="foreign",
               SSH_AUTH_SOCK="foreign", GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="core.sshCommand",
               GIT_CONFIG_VALUE_0="foreign", HAPPYRANCH_TASK_TMP_ROOT="foreign", JENKINS_NODE_COOKIE="controlled-cookie")
    result = subprocess.run(["/bin/bash", "-p", "-c", _controlled_prepare_shell()], env=env, capture_output=True, timeout=8)
    assert _shell_exit(result) == 0, result.stderr
    root = Path(env["WORKSPACE"]) / "thr211-1"
    receipt = json.loads((root / "artifacts/shell-result.json").read_text())
    assert receipt == {"primary_exit": 0, "pytest_exit": None,
                       "cleanup": "UNKNOWN", "observer": "UNAVAILABLE", "admission": "operator-window"}
    child_env = dict(line.split("=", 1) for line in Path(str(trace) + ".env").read_text().splitlines())
    assert "credential-canary" not in str(child_env)
    assert all(name not in child_env for name in ("BASH_ENV", "PYTHONPATH", "SSH_AUTH_SOCK", "GIT_CONFIG_COUNT", "FAKE_CLAUDE_PLAN", "HAPPYRANCH_TASK_TMP_ROOT"))
    assert child_env["JENKINS_NODE_COOKIE"] == "controlled-cookie"
    for leaf in ("home", "xdg-config", "xdg-cache", "xdg-state", "xdg-runtime", "tmp", "uv-cache", "venv", "daemon-home", "plans", "artifacts"):
        assert (root / leaf).is_dir()
        assert (root / leaf).stat().st_mode & 0o077 == 0
    calls = trace.read_text()
    assert "--no-checkout" in calls and "checkout --detach " + "a" * 40 in calls
    assert "uv sync --frozen --python " + env["ADMITTED_PYTHON"] in calls
    assert "pytest " not in calls
    emitted_python = Path(str(trace) + ".python").read_text()
    assert "sys.path.insert(0, str(source))" in emitted_python
    assert "prepare_pipeline_environment" in emitted_python


@pytest.mark.parametrize("key,value", [
    ("REQUESTED_REQUEST", "x"), ("REQUESTED_SOURCE", "e" * 40), ("REQUESTED_PIPELINE", "f" * 64),
    ("REQUESTED_MODE", "ABORT"), ("ADMITTED_REQUEST", "bad request"), ("ADMITTED_SOURCE", "bad"),
    ("ADMITTED_PIPELINE", "x"), ("ADMITTED_CONFIG", "x"), ("ADMITTED_LOCK", "x"),
    ("NODE_NAME", "foreign"), ("ADMITTED_ACCOUNT", "foreign"), ("ADMITTED_START", "bad"),
    ("ADMITTED_EXPIRY", "1000000000"), ("ADMITTED_START", "9999999999"),
    ("ADMITTED_PYTHON", "python3"), ("BUILD_NUMBER", "../escape"),
    ("ADMITTED_UID", "0"), ("ADMITTED_UID", "998"), ("ADMITTED_HOST", "other"),
    ("REQUESTED_EVALUATED", "x"), ("ADMITTED_READY", "/absent/ready"),
])
def test_actual_pipeline_rejects_untrusted_admission_before_tools(tmp_path, key, value):
    env, trace = _pipeline_tools(tmp_path)
    env[key] = value
    if key in ("ADMITTED_REQUEST", "ADMITTED_SOURCE", "ADMITTED_PIPELINE"):
        env[key.replace("ADMITTED_", "REQUESTED_")] = value
    result = subprocess.run(["/bin/bash", "-p", "-c", _controlled_prepare_shell()], env=env, capture_output=True, timeout=5)
    assert _shell_exit(result) == 64, result.stderr
    assert not trace.exists()
    assert list(Path(env["WORKSPACE"]).iterdir()) == []


@pytest.mark.parametrize("failure,expected", [("clone", 37), ("head", 1), ("python", 38), ("uv", 39), ("abort", 143), ("publication", 37)])
def test_actual_pipeline_failure_abort_and_publication_preserve_primary(tmp_path, failure, expected):
    env, trace = _pipeline_tools(tmp_path, failure=failure)
    result = subprocess.run(["/bin/bash", "-p", "-c", _controlled_prepare_shell()], env=env, capture_output=True, timeout=8)
    assert _shell_exit(result) == expected, result.stderr
    artifacts = Path(env["WORKSPACE"]) / "thr211-1/artifacts"
    if failure == "publication":
        assert any("shell-receipt:FileExistsError" in error for error in _acquired(result)["errors"])
    else:
        receipt = json.loads((artifacts / "shell-result.json").read_text())
        assert receipt["primary_exit"] == expected and receipt["pytest_exit"] is None
        assert receipt["cleanup"] == "UNKNOWN"
    assert "pytest " not in trace.read_text()


@pytest.mark.parametrize("failure", [None, "pipeline", "lock", "arch", "version"])
def test_actual_emitted_pipeline_identity_python(tmp_path, failure):
    import hashlib
    import platform
    shell = _pipeline_shell()
    code = shell.split("<<'THR211_IDENTITY'\n", 1)[1].split("\nTHR211_IDENTITY", 1)[0]
    (tmp_path / "Jenkinsfile").write_text("controlled-pipeline")
    (tmp_path / "uv.lock").write_text("controlled-lock")
    digests = [hashlib.sha256((tmp_path / file).read_bytes()).hexdigest() for file in ("Jenkinsfile", "uv.lock")]
    if failure in ("pipeline", "lock"):
        digests[0 if failure == "pipeline" else 1] = "0" * 64
    args = ["probe", *digests, "wrong" if failure == "arch" else platform.machine()]
    # Controlled version inputs let the same shipping gate be exercised by
    # required CI3.14 as well as maker3.12, without copying the gate logic.
    setup = f"import sys; sys.version_info = {(3, 11) if failure == 'version' else (3, 12)!r}; sys.argv = {args!r}\n"
    result = subprocess.run([sys.executable, "-I", "-c", setup + code], cwd=tmp_path, capture_output=True, timeout=5)
    assert (result.returncode == 0) is (failure is None)
    if failure:
        assert b"AssertionError" in result.stderr
    else:
        assert b"native" in result.stdout


@pytest.mark.parametrize("case", ["alias", "ancestor", "preexisting", "dangling"])
def test_actual_pipeline_rejects_foreign_paths_without_touching_them(tmp_path, case):
    env, trace = _pipeline_tools(tmp_path)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    sentinel = foreign / "sentinel"
    sentinel.write_text("untouched")
    inode = sentinel.stat().st_ino
    if case in ("alias", "ancestor"):
        alias = tmp_path / "alias"
        alias.symlink_to(foreign, target_is_directory=True)
        if case == "ancestor":
            (foreign / "nested").mkdir()
            env["WORKSPACE"] = str(alias / "nested")
        else:
            env["WORKSPACE"] = str(alias)
    else:
        (Path(env["WORKSPACE"]) / "thr211-1").symlink_to(foreign if case == "preexisting" else foreign / "absent")
    result = subprocess.run(["/bin/bash", "-p", "-c", _controlled_prepare_shell()], env=env, capture_output=True, timeout=5)
    assert _shell_exit(result) != 0
    assert not trace.exists()
    assert sentinel.stat().st_ino == inode and sentinel.read_text() == "untouched"


def _extracted_fixture(name, monkeypatch, tmp_path, scenario, *, spawn=None):
    """Only compile the named shipping fixture, never import its directory.

    The real foreground context handles acquisition and cleanup; only Popen,
    health and runtime registration are controlled. No daemon is imported.
    """
    import ast
    import builtins
    import types
    source = Path(__file__).parent / "integration/conftest.py"
    tree = ast.parse(source.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    node.decorator_list = []
    calls = []
    def launch(command, **kwargs):
        calls.append(("start", kwargs))
        if spawn:
            spawn()
        if scenario in ("start", "post_spawn"):
            raise RuntimeError(scenario)
        def wait(**options):
            calls.append(("stop", options))
            if scenario in ("stop_timeout", "simultaneous"):
                raise subprocess.TimeoutExpired(command, options["timeout"])
            return 37 if scenario == "stop_nonzero" else 0
        return types.SimpleNamespace(args=command, wait=wait, poll=lambda: None)
    # Replace the helper's module binding, not the shared subprocess module.
    monkeypatch.setattr(containment, "subprocess", types.SimpleNamespace(
        Popen=launch, CalledProcessError=subprocess.CalledProcessError))
    ticks = iter(range(0, 100, 2))
    port_file = tmp_path / "port"
    port_file.write_text("49123")
    registry = types.SimpleNamespace(save_registry=lambda _: None)
    runtimes = types.SimpleNamespace(register=lambda _: None)
    def importer(module, globals=None, locals=None, fromlist=(), level=0):
        if module == "runtime.orchestrator.executor_binary_registry":
            return registry
        if module == "runtime.daemon":
            return types.SimpleNamespace(runtimes=runtimes)
        raise AssertionError(f"unexpected fixture import: {module}")
    ns = {"__file__": str(source), "__builtins__": dict(vars(builtins), __import__=importer),
          "Path": Path, "foreground_daemon": containment.foreground_daemon, "_nested_daemon_env": lambda: {},
          "time": types.SimpleNamespace(monotonic=lambda: next(ticks), sleep=lambda _: None),
          "paths_mod": types.SimpleNamespace(port_file=lambda: port_file),
          "httpx": types.SimpleNamespace(get=lambda *a, **k: types.SimpleNamespace(status_code=503 if scenario == "health" else 200), HTTPError=OSError)}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), ns)
    arguments = {arg.arg: monkeypatch if arg.arg == "monkeypatch" else tmp_path for arg in node.args.args}
    return ns[name](**arguments), calls


@pytest.mark.parametrize("fixture", ["live_daemon", "live_daemon_idle"])
@pytest.mark.parametrize("scenario", ["normal", "start", "health", "assertion", "post_spawn", "stop_nonzero", "stop_timeout", "simultaneous", "parent_abort", "observer_loss"])
def test_shipping_fixture_foreground_cleanup(tmp_path, monkeypatch, fixture, scenario):
    generator, calls = _extracted_fixture(fixture, monkeypatch, tmp_path, scenario)
    if scenario in ("start", "post_spawn", "health"):
        with pytest.raises(RuntimeError):
            next(generator)
        assert [call[0] for call in calls] == (["start", "stop"] if scenario == "health" else ["start"])
        # The lease descriptors are gone even when Popen raises after spawn.
        with pytest.raises(OSError):
            os.fstat(calls[0][1]["stdin"])
    else:
        assert next(generator) == "49123"
        if scenario in ("assertion", "simultaneous"):
            primary = AssertionError("original")
            with pytest.raises(AssertionError) as caught:
                generator.throw(primary)
            assert caught.value is primary
            assert len(calls) == 2
            assert len(primary.thr211_cleanup_errors) == (1 if scenario == "simultaneous" else 0)
        elif scenario in ("parent_abort", "observer_loss"):
            generator.close()
            assert len(calls) == 2  # Generator exit is not native parent-loss proof.
        elif scenario in ("stop_timeout", "stop_nonzero"):
            with pytest.raises(BaseExceptionGroup) as caught:
                next(generator)
            expected = subprocess.TimeoutExpired if scenario == "stop_timeout" else subprocess.CalledProcessError
            assert isinstance(caught.value.exceptions[0], expected)
        else:
            with pytest.raises(StopIteration):
                next(generator)
            assert calls[-1][0] == "stop"
    assert all(kwargs["timeout"] == 4 for name, kwargs in calls if name == "stop")


@pytest.mark.parametrize("fixture", ["live_daemon", "live_daemon_idle"])
def test_shipping_start_gap_leaves_controlled_escaped_listener_until_own_expiry(tmp_path, monkeypatch, fixture):
    import socket
    import select
    # Sole synthetic child. It sets its own alarm before acquiring its listener,
    # has no children and expires independently; no parent PID/PGID is signalled.
    child = None
    port = None
    code = """import signal, socket, time
signal.alarm(2)
s = socket.socket()
s.bind(('127.0.0.1', 0))
s.listen()
print(s.getsockname()[1], flush=True)
time.sleep(1)
s.close()
"""
    def spawn():
        nonlocal child, port
        child = subprocess.Popen([sys.executable, "-I", "-c", code], stdout=subprocess.PIPE, text=True,
                                 start_new_session=True, env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
        # select bounds even a failure before the first line/registration.
        assert select.select([child.stdout], [], [], 2)[0]
        port = int(child.stdout.readline())
    sentinel = tmp_path / "unrelated"
    sentinel.write_text("untouched")
    inode = sentinel.stat().st_ino
    unrelated = subprocess.Popen([sys.executable, "-I", "-c",
                                  "import signal,time; signal.alarm(3); print('ready',flush=True); time.sleep(2)"],
                                 stdout=subprocess.PIPE, text=True,
                                 env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    generator, calls = _extracted_fixture(fixture, monkeypatch, tmp_path, "post_spawn", spawn=spawn)
    try:
        assert select.select([unrelated.stdout], [], [], 2)[0]
        assert unrelated.stdout.readline().strip() == "ready"
        with pytest.raises(RuntimeError, match="post_spawn"):
            next(generator)
        assert child.poll() is None
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            pass  # Actual escaped listener survives the SHIPPING failure path.
        assert [c[0] for c in calls] == ["start"]
        assert unrelated.poll() is None
    finally:
        if child is not None:
            child.wait(timeout=3)  # Cooperative self-expiry, not fixture cleanup.
            child.stdout.close()
        unrelated.wait(timeout=4)
        unrelated.stdout.close()
    assert unrelated.returncode == 0
    assert sentinel.stat().st_ino == inode and sentinel.read_text() == "untouched"
    with socket.socket() as observer:
        observer.settimeout(0.2)
        assert observer.connect_ex(("127.0.0.1", port)) != 0
    # This Linux/Mac-compatible synthetic counterexample proves insufficiency;
    # it establishes no native-Mac daemon lifetime or independent observer proof.


def test_allocation_console_failure_retains_classified_secondary_without_workspace_fallback(tmp_path) -> None:
    """Evaluate shipping Pipeline using the retained TASK8153 reproducer."""
    jar = os.environ.get("THR211_GROOVY_JAR")
    if not jar:
        pytest.skip("evaluated Pipeline requires the task-provisioned Groovy jar")
    probe = tmp_path / "allocation.groovy"
    probe.write_text(ALLOCATION_PROBE_GROOVY)
    env, _ = _pipeline_tools(tmp_path)
    inputs = tmp_path / "input.json"
    inputs.write_text(json.dumps({"allocation": {"env": env}}))
    result = subprocess.run([
        "/usr/bin/java", f"-Djava.io.tmpdir={tmp_path}", "-cp", jar,
        "groovy.ui.GroovyMain", str(probe),
        str(Path(__file__).parents[1] / "Jenkinsfile"), str(inputs),
    ], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["passed"] is True


ALLOCATION_PROBE_GROOVY = r'''
import groovy.json.JsonOutput
import groovy.json.JsonSlurperClassic
def input = new JsonSlurperClassic().parseText(new File(args[1]).text).allocation
def installed = input.env.findAll { k,v -> k.startsWith('ADMITTED_') }.collectEntries { k,v -> [(k.substring(9)):v] }
def b = new Binding()
b.setVariable('THR211_INSTALL', installed)
b.setVariable('params', [:])
b.setVariable('env', [BUILD_NUMBER:'1'])
b.setVariable('currentBuild', [result:null])
b.setVariable('properties', { List x -> })
b.setVariable('disableConcurrentBuilds', { -> [:] })
b.setVariable('parameters', { List x -> x })
b.setVariable('choice', { Map x -> x })
b.setVariable('string', { Map x -> x })
b.setVariable('parallel', { Map x -> x.execution(); x.allocation() })
def primary = new IllegalArgumentException('allocation unavailable')
def secondary = new IOException('console unavailable')
def calls = []
b.setVariable('node', { String x, Closure body -> calls.add('node'); throw primary })
b.setVariable('echo', { String x -> calls.add('console attempted'); throw secondary })
['sh','writeFile','archiveArtifacts'].each { name ->
  b.setVariable(name, { Map x -> calls.add(name); throw new AssertionError('forbidden filesystem operation: '+name) })
}
Throwable thrown
try { new GroovyShell(b).evaluate(new File(args[0])) } catch(Throwable e) { thrown=e }
assert thrown.is(primary)
assert calls == ['node', 'console attempted']
assert thrown.suppressed.length == 1
assert thrown.suppressed[0].message == 'console:IOException'
assert thrown.suppressed[0].cause.is(secondary)
assert thrown.cause == null
assert b.getVariable('currentBuild').result == 'FAILURE'
println(JsonOutput.toJson([passed:true, primary:thrown.message,
    returned_suppressed:thrown.suppressed*.message, calls:calls]))
'''


def test_allocated_console_failure_retains_exact_causal_objects(tmp_path) -> None:
    """Evaluate the shipping allocated body with retained Pipeline doubles."""
    jar = os.environ.get("THR211_GROOVY_JAR")
    if not jar:
        pytest.skip("evaluated Pipeline requires the task-provisioned Groovy jar")
    probe = tmp_path / "allocated-console.groovy"
    probe.write_text(ALLOCATED_CONSOLE_PROBE_GROOVY)
    env, _ = _pipeline_tools(tmp_path)
    inputs = tmp_path / "input.json"
    inputs.write_text(json.dumps({"allocation": {"env": env}}))
    result = subprocess.run([
        "/usr/bin/java", f"-Djava.io.tmpdir={tmp_path}", "-cp", jar,
        "groovy.ui.GroovyMain", str(probe),
        str(Path(__file__).parents[1] / "Jenkinsfile"), str(inputs),
    ], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["passed"] == ["primary", "abort", "console-only", "multiple"]


ALLOCATED_CONSOLE_PROBE_GROOVY = r'''
import groovy.json.JsonOutput
import groovy.json.JsonSlurperClassic
def input = new JsonSlurperClassic().parseText(new File(args[1]).text).allocation
def installed = input.env.findAll { k,v -> k.startsWith('ADMITTED_') }.collectEntries { k,v -> [(k.substring(9)):v] }
def passed = []
['primary', 'abort', 'console-only', 'multiple'].each { scenario ->
  def b = new Binding()
  b.setVariable('THR211_INSTALL', installed)
  b.setVariable('params', [REQUEST_ID:installed.REQUEST, MODE:installed.MODE, SOURCE_SHA:installed.SOURCE, PIPELINE_SHA:installed.PIPELINE])
  b.setVariable('env', [BUILD_NUMBER:'1'])
  b.setVariable('currentBuild', [result:null])
  b.setVariable('properties', { List x -> })
  b.setVariable('disableConcurrentBuilds', { -> [:] })
  b.setVariable('parameters', { List x -> x })
  b.setVariable('choice', { Map x -> x })
  b.setVariable('string', { Map x -> x })
  b.setVariable('parallel', { Map x -> x.execution(); x.allocation() })
  def primary = scenario == 'abort' ? new InterruptedException('aborted') : new IllegalArgumentException('workload primary')
  def console = new IOException('exact console object')
  def calls = []
  def childEnv = [:]
  b.setVariable('node', { String x, Closure body -> calls.add('node'); body() })
  b.setVariable('pwd', { -> input.env.WORKSPACE })
  b.setVariable('stage', { String x, Closure body ->
    // This controlled branch covers aggregation with no earlier primary. It
    // does not substitute for the actual workload-shell tests.
    if (x == 'Workload') {
      if (scenario == 'console-only') return
      throw primary
    }
    body()
  })
  b.setVariable('timeout', { Map x, Closure body -> body() })
  b.setVariable('withEnv', { List x, Closure body ->
    def old = [:] + childEnv
    x.each { def parts = it.toString().split('=', 2); childEnv[parts[0]] = parts[1] }
    try { body() } finally { childEnv = old }
  })
  b.setVariable('sh', { Map x ->
    assert x.returnStdout
    if (childEnv.containsKey('PUBLICATION_ROOT')) {
      calls.add('writer')
      return 'THR211_ACQUIRED=' + JsonOutput.toJson([published:true, nonce:childEnv.PUBLICATION_NONCE, root:childEnv.PUBLICATION_ROOT, errors:[]])
    }
    calls.add('prepare')
    return 'THR211_ACQUIRED=' + JsonOutput.toJson([nonce:childEnv.PUBLICATION_NONCE, root:input.env.WORKSPACE+'/thr211-1', ancestry:[[device:1,inode:2]], exit:0, errors:[]]) + '\nTHR211_EXIT=0\n'
  })
  b.setVariable('archiveArtifacts', { Map x ->
    calls.add('archive')
    assert x.allowEmptyArchive == false && x.followSymlinks == false
    if (scenario == 'multiple') throw new IOException('archive unavailable')
  })
  b.setVariable('writeFile', { Map x -> throw new AssertionError('unsafe writeFile') })
  b.setVariable('error', { String x -> throw primary })
  b.setVariable('echo', { String x -> calls.add('console'); throw console })
  Throwable thrown
  try { new GroovyShell(b).evaluate(new File(args[0])) } catch (Throwable e) { thrown = e }
  assert calls == ['node', 'prepare', 'writer', 'archive', 'archive', 'console']
  assert b.getVariable('currentBuild').result == 'FAILURE'
  if (scenario == 'console-only') {
    assert thrown.is(console)
    assert thrown.suppressed.length == 0
    assert thrown.cause == null
  } else {
    assert thrown.is(primary)
    assert thrown.cause == null
    def classified = thrown.suppressed.findAll { it.message == 'console:IOException' }
    assert classified.size() == 1
    assert classified[0].cause.is(console)
    assert thrown.suppressed.length == (scenario == 'multiple' ? 3 : 1)
    if (scenario == 'multiple') assert thrown.suppressed*.message == ['receipt-archive:IOException', 'artifacts:IOException', 'console:IOException']
  }
  passed.add(scenario)
}
println(JsonOutput.toJson([passed:passed]))
'''


def _await_file(path: Path, timeout: float = 2) -> str:
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and path.read_text():
            return path.read_text()
        time.sleep(.01)
    raise AssertionError(f"synthetic readiness deadline: {path}")


@pytest.mark.parametrize("failure", ["normal", "assertion", "deadline", "observer_loss"])
def test_native_foreground_interpreter_lifetime(tmp_path, failure):
    import socket
    ready = tmp_path / "ready"
    (tmp_path / "synthetic.py").write_text(
        "import os, signal, socket, time\n"
        "signal.alarm(3)\ns = socket.socket()\ns.bind(('127.0.0.1',0))\ns.listen()\n"
        f"with open({str(ready)!r}, 'x') as f: f.write(str(s.getsockname()[1]))\n"
        + ("time.sleep(.1)\nsignal.raise_signal(signal.SIGKILL)\n" if failure == "observer_loss" else "time.sleep(2)\n"))
    primary = AssertionError("body failed")
    process = None
    try:
        with containment.foreground_daemon(tmp_path, dict(os.environ), module="synthetic", lifetime=.5, grace=.1) as process:
            port = int(_await_file(ready))
            with socket.create_connection(("127.0.0.1", port), timeout=.2):
                pass
            if failure == "assertion":
                raise primary
            if failure in ("deadline", "observer_loss"):
                process.wait(timeout=2)
    except AssertionError as error:
        assert failure == "assertion" and error is primary
        assert error.thr211_cleanup_errors == ()
    except BaseExceptionGroup as error:
        assert failure in ("deadline", "observer_loss")
        assert error.exceptions[0].returncode == (124 if failure == "deadline" else -9)
    assert process is not None and process.returncode is not None
    with socket.socket() as observer:
        observer.settimeout(.2)
        assert observer.connect_ex(("127.0.0.1", port)) != 0


def test_native_parent_loss_closes_foreground_listener(tmp_path):
    import socket
    import time
    ready = tmp_path / "ready"
    (tmp_path / "synthetic.py").write_text(
        "import signal,socket,time\nsignal.alarm(3)\n"
        "s=socket.socket(); s.bind(('127.0.0.1',0)); s.listen()\n"
        f"with open({str(ready)!r},'x') as f: f.write(str(s.getsockname()[1]))\n"
        "time.sleep(2)\n")
    source = str(Path(__file__).parents[1])
    parent_code = (
        f"import sys; sys.path.insert(0,{source!r})\n"
        "import os,time\nfrom pathlib import Path\nfrom tests.thr211_containment import foreground_daemon\n"
        f"with foreground_daemon(Path({str(tmp_path)!r}),dict(os.environ),module='synthetic',lifetime=2,grace=.1):\n"
        f" while not Path({str(ready)!r}).exists(): time.sleep(.01)\n"
        " os._exit(23)\n")
    result = subprocess.run([sys.executable, "-I", "-c", parent_code], timeout=4)
    assert result.returncode == 23
    port = int(_await_file(ready))
    deadline = time.monotonic() + 1
    while True:
        with socket.socket() as observer:
            observer.settimeout(.1)
            if observer.connect_ex(("127.0.0.1", port)) != 0:
                break
        assert time.monotonic() < deadline
        time.sleep(.01)
    # Port closure proves only this listener; no grandchild-reaping claim.


@pytest.mark.parametrize("loss", ["lease_close", "observer_loss"])
def test_native_foreground_escape_remains_unknown(tmp_path, loss):
    """Specific F04 counterexample even with foreground launch and EOF cleanup."""
    import socket
    import time
    port_file = tmp_path / "escaped-port"
    finished = tmp_path / "escaped-finished"
    sentinel = tmp_path / "unrelated"
    sentinel.write_text("untouched")
    inode = sentinel.stat().st_ino
    child_code = (
        "import signal,socket,time\nsignal.alarm(3)\n"
        "s=socket.socket(); s.bind(('127.0.0.1',0)); s.listen()\n"
        f"with open({str(port_file)!r},'x') as f: f.write(str(s.getsockname()[1]))\n"
        "time.sleep(1)\ns.close()\n"
        f"with open({str(finished)!r},'x') as f: f.write('self-expired')\n")
    (tmp_path / "synthetic.py").write_text(
        "import subprocess,sys,time,signal\n"
        f"subprocess.Popen([sys.executable,'-I','-c',{child_code!r}], start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        + ("time.sleep(.2)\nsignal.raise_signal(signal.SIGKILL)\n" if loss == "observer_loss" else "time.sleep(2)\n"))
    unrelated = subprocess.Popen([sys.executable, "-I", "-c", "import signal,time; signal.alarm(4); time.sleep(2)"], env=dict(os.environ))
    try:
        try:
            with containment.foreground_daemon(tmp_path, dict(os.environ), module="synthetic", lifetime=2, grace=.1) as process:
                port = int(_await_file(port_file))
                if loss == "observer_loss":
                    process.wait(timeout=1)
        except BaseExceptionGroup as error:
            assert loss == "observer_loss" and error.exceptions[0].returncode == -9
        assert process.returncode is not None
        with socket.create_connection(("127.0.0.1", port), timeout=.2):
            pass  # Escaped child/listener survives the shipping helper's cleanup.
        assert unrelated.poll() is None
        assert _await_file(finished) == "self-expired"
    finally:
        unrelated.wait(timeout=4)
    assert unrelated.returncode == 0
    assert sentinel.stat().st_ino == inode and sentinel.read_text() == "untouched"
    with socket.socket() as observer:
        observer.settimeout(.2)
        assert observer.connect_ex(("127.0.0.1", port)) != 0


def test_foreground_preserves_primary_and_all_cleanup_error_objects(monkeypatch, tmp_path):
    import types
    primary = KeyboardInterrupt("abort")
    close_errors = [OSError("writer close"), OSError("reader close")]
    wait_error = subprocess.TimeoutExpired("synthetic", 4)
    calls = []
    def close(fd):
        calls.append(fd)
        raise close_errors[len(calls) - 1]
    def wait(**kwargs):
        assert kwargs == {"timeout": 4}
        raise wait_error
    monkeypatch.setattr(containment, "os", types.SimpleNamespace(pipe=lambda: (10, 11), close=close))
    monkeypatch.setattr(containment, "subprocess", types.SimpleNamespace(Popen=lambda *a, **k: types.SimpleNamespace(wait=wait)))
    with pytest.raises(KeyboardInterrupt) as caught:
        with containment.foreground_daemon(tmp_path, {}):
            raise primary
    assert caught.value is primary
    assert calls == [11, 10]
    assert primary.thr211_cleanup_errors == (*close_errors, wait_error)


def test_foreground_acquisition_failure_keeps_exact_primary(monkeypatch, tmp_path):
    import types
    primary = OSError("pipe acquisition")
    def acquire():
        raise primary
    monkeypatch.setattr(containment, "os", types.SimpleNamespace(pipe=acquire))
    with pytest.raises(OSError) as caught:
        with containment.foreground_daemon(tmp_path, {}):
            pytest.fail("must not launch")
    assert caught.value is primary and primary.thr211_cleanup_errors == ()


def test_native_post_spawn_pre_registration_failure_releases_lease(monkeypatch, tmp_path):
    import types
    original = RuntimeError("post-spawn/pre-registration")
    children = []
    readers = []
    def launch(*args, **kwargs):
        child = subprocess.Popen(*args, **kwargs)
        children.append(child)
        readers.append(kwargs["stdin"])
        raise original
    (tmp_path / "synthetic.py").write_text("import time; time.sleep(2)\n")
    monkeypatch.setattr(containment, "subprocess", types.SimpleNamespace(Popen=launch))
    with pytest.raises(RuntimeError) as caught:
        with containment.foreground_daemon(tmp_path, dict(os.environ), module="synthetic", lifetime=1, grace=.1):
            pytest.fail("must not yield")
    assert caught.value is original
    assert len(children) == 1
    assert children[0].wait(timeout=2) == -15
    with pytest.raises(OSError):
        os.fstat(readers[0])


@pytest.mark.parametrize("lifetime,grace", [(0, 1), (1801, 1), (1, 0), (1, 6), (float('nan'), 1)])
def test_foreground_invalid_bounds_fail_before_acquisition(monkeypatch, tmp_path, lifetime, grace):
    import types
    monkeypatch.setattr(containment, "os", types.SimpleNamespace())
    with pytest.raises(ValueError):
        with containment.foreground_daemon(tmp_path, {}, lifetime=lifetime, grace=grace):
            pytest.fail("must not launch")


@pytest.mark.parametrize("failure", [None, "version", "arch", "env", "origin", "registry", "symlink", "publication-symlink", "publication-file"])
def test_actual_pipeline_environment_probe(tmp_path, monkeypatch, failure):
    import importlib
    import platform
    from types import SimpleNamespace
    from runtime.orchestrator import executor_binary_registry as registry
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    leaves = {"HOME": "home", "XDG_CONFIG_HOME": "xdg-config", "XDG_CACHE_HOME": "xdg-cache",
              "XDG_STATE_HOME": "xdg-state", "XDG_DATA_HOME": "xdg-state", "XDG_RUNTIME_DIR": "xdg-runtime",
              "TMPDIR": "tmp", "TMP": "tmp", "TEMP": "tmp", "UV_CACHE_DIR": "uv-cache",
              "UV_PROJECT_ENVIRONMENT": "venv/env", "HAPPYRANCH_DAEMON_HOME": "daemon-home"}
    for leaf in (*set(leaves.values()), "bin", "plans", "artifacts"):
        (root / leaf).mkdir(mode=0o700, parents=True, exist_ok=True)
    for key, leaf in leaves.items():
        monkeypatch.setenv(key, str(root / leaf))
    monkeypatch.setattr(sys, "prefix", str(root / "venv/env"))
    monkeypatch.setattr(sys, "_base_executable", sys.executable)
    monkeypatch.setattr(sys, "version_info", (3, 13) if failure == "version" else (3, 12))
    source = root / "source"
    source.mkdir()
    (source / "tests/integration").mkdir(parents=True)
    (source / "uv.lock").write_text("controlled-lock")
    for name in ("claude", "codex", "opencode"):
        (source / f"tests/integration/fake_{name}.sh").write_text("#!/bin/sh\nexit 0\n")
    monkeypatch.setattr(importlib, "import_module", lambda name: SimpleNamespace(__file__=str(
        tmp_path / "foreign.py" if failure == "origin" else source / "module.py")))
    saved = {}
    monkeypatch.setattr(registry, "save_registry", lambda data: saved.update(data))
    monkeypatch.setattr(registry, "load_registry", lambda: {} if failure == "registry" else saved)
    if failure == "env":
        monkeypatch.setenv("HOME", str(tmp_path))
    if failure == "symlink":
        (root / "home").rmdir()
        (root / "home").symlink_to(tmp_path)
    if failure in ("publication-symlink", "publication-file"):
        foreign = tmp_path / "foreign-publication"
        foreign.mkdir()
        marker = foreign / "preparation.json"
        marker.write_text("FOREIGN")
        inode = marker.stat().st_ino
        if failure == "publication-symlink":
            (root / "artifacts").rmdir()
            (root / "artifacts").symlink_to(foreign)
        else:
            (root / "artifacts/preparation.json").symlink_to(marker)
        with pytest.raises(BaseExceptionGroup):
            containment.prepare_pipeline_environment(source, root, Path(sys.executable), platform.machine())
        assert marker.stat().st_ino == inode and marker.read_text() == "FOREIGN"
        return
    if failure:
        with pytest.raises(RuntimeError):
            containment.prepare_pipeline_environment(source, root, Path(sys.executable), "wrong" if failure == "arch" else platform.machine())
        assert not (root / "artifacts/preparation.json").exists()
    else:
        receipt = containment.prepare_pipeline_environment(source, root, Path(sys.executable), platform.machine())
        assert receipt["result"] == "PREPARED" and receipt["pytest_exit"] is None and receipt["port"] is None
        assert json.loads((root / "artifacts/preparation.json").read_text()) == receipt
        assert saved == {name: str(root / f"bin/fake_{name}.sh") for name in ("claude", "codex", "opencode")}
        for path in saved.values():
            assert Path(path).stat().st_mode & 0o777 == 0o700

from tests.thr211_containment import (
    build_review_required_job_script,
    plan_environment,
    prepare_private_test_paths,
)


def test_private_plan_paths_are_fresh_private_and_explicit(tmp_path: Path) -> None:
    paths = prepare_private_test_paths(tmp_path / "candidate")

    assert paths.plans.is_dir()
    assert paths.artifacts.is_dir()
    assert plan_environment(paths) == {"HAPPYRANCH_TEST_PLAN_DIR": str(paths.plans)}
    assert paths.plans.stat().st_mode & 0o077 == 0


def test_private_plan_paths_refuse_existing_root(tmp_path: Path) -> None:
    root = tmp_path / "candidate"
    root.mkdir()

    with pytest.raises(FileExistsError):
        prepare_private_test_paths(root)


def test_private_plan_paths_refuse_symlink_ancestor_without_touching_target(tmp_path: Path) -> None:
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(foreign, target_is_directory=True)

    with pytest.raises(OSError):
        prepare_private_test_paths(alias / "candidate")

    assert list(foreign.iterdir()) == []


def test_private_plan_paths_refuse_nested_symlink_ancestor_without_touching_target(tmp_path: Path) -> None:
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "alias").symlink_to(foreign, target_is_directory=True)

    with pytest.raises(OSError):
        prepare_private_test_paths(nested / "alias" / "candidate")

    assert list(foreign.iterdir()) == []


def test_private_plan_paths_refuse_non_directory_ancestor(tmp_path: Path) -> None:
    ancestor = tmp_path / "not-a-directory"
    ancestor.write_text("foreign")

    with pytest.raises(NotADirectoryError):
        prepare_private_test_paths(ancestor / "candidate")


def test_review_required_job_script_is_exclusive_and_private(tmp_path: Path) -> None:
    paths = prepare_private_test_paths(tmp_path / "candidate with spaces")
    sentinel = paths.plans / "job's sentinel"
    script = build_review_required_job_script(sentinel)
    assert subprocess.run(["bash", "-c", script], capture_output=True).returncode == 0
    assert sentinel.stat().st_mode & 0o077 == 0
    inode = sentinel.stat().st_ino
    assert subprocess.run(["bash", "-c", script], capture_output=True).returncode != 0
    assert sentinel.stat().st_ino == inode


def test_review_required_job_script_refuses_foreign_symlink(tmp_path: Path) -> None:
    paths = prepare_private_test_paths(tmp_path / "candidate")
    foreign = tmp_path / "foreign"
    foreign.write_text("do not alter")
    sentinel = paths.plans / "sentinel"
    sentinel.symlink_to(foreign)
    assert subprocess.run(
        ["bash", "-c", build_review_required_job_script(sentinel)], capture_output=True
    ).returncode != 0
    assert foreign.read_text() == "do not alter"


@pytest.mark.parametrize("family", ["threads", "thread_reply", "review_required", "persistent", "blocked_multi", "blocked_autonomous", "content"])
def test_shipping_plan_builder_exists(family: str) -> None:
    assert callable(getattr(containment, f"build_{family}_plan", None))


def test_private_paths_compensate_partial_acquisition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_mkdir = os.mkdir

    def fail_artifacts(path: str, *args: object, **kwargs: object) -> None:
        if path == "artifacts":
            raise OSError("primary artifacts acquisition")
        real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(os, "mkdir", fail_artifacts)
    root = tmp_path / "partial"
    with pytest.raises(OSError, match="primary artifacts acquisition"):
        prepare_private_test_paths(root)
    assert not root.exists()


# These stubs record the real generated shell's callback files, never talk to a
# daemon, and leave the submitted job bodies for independent execution below.
_TOOL_STUB = r'''
import json, os, pathlib, signal, subprocess, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
root = pathlib.Path(os.environ['CONTROL_ROOT'])
receipt = root / 'calls.jsonl'
calls = [json.loads(line) for line in receipt.read_text().splitlines()] if receipt.exists() else []
if name in ('happyranch', 'curl'):
    source = pathlib.Path(args[args.index('--from-file') + 1] if name == 'happyranch' else args[args.index('-d') + 1][1:])
    info = source.lstat()
    payload = json.loads(source.read_text())
    entry = dict(tool=name, args=args, path=str(source), payload=payload,
                 mode=info.st_mode, uid=info.st_uid, inode=info.st_ino,
                 parent_mode=source.parent.stat().st_mode)
    with receipt.open('a') as stream:
        stream.write(json.dumps(entry) + '\n')
    number = len(calls) + 1
    if str(number) == os.environ.get('ABORT_CALLBACK'):
        os.kill(os.getppid(), signal.SIGTERM)
    if str(number) == os.environ.get('FAIL_CALLBACK'):
        print('controlled primary callback error', file=sys.stderr)
        sys.exit(37)
    if args[:2] == ['jobs', 'submit']:
        count = sum(c['args'][:2] == ['jobs', 'submit'] for c in calls) + 1
        print(f'ok: submitted JOB-{count:03d} (status=pending).')
    sys.exit(0)
if name == 'sleep':
    with (root / 'sleeps.jsonl').open('a') as stream:
        stream.write(json.dumps(args) + '\n')
    if args == ['0.1']:
        stop = pathlib.Path(os.environ['CONTROL_PLANS']) / 'founder-acted.txt'
        with stop.open('x'):
            stop.chmod(0o600)
    sys.exit(0)
if name in ('rm', 'rmdir'):
    with (root / 'cleanup.jsonl').open('a') as stream:
        stream.write(json.dumps(dict(tool=name, args=args)) + '\n')
    if os.environ.get('FAIL_CLEANUP') == '1':
        print('controlled secondary ' + name, file=sys.stderr)
        sys.exit(51 if name == 'rm' else 52)
os.execv('/bin/' + name, [name, *args])
'''


class PlanRun:
    def __init__(self, root: Path, family: str) -> None:
        root.mkdir(mode=0o700)
        self.root = root
        self.family = family
        self.paths = prepare_private_test_paths(root / "owned root's space")
        self.bin = root / 'bin'
        self.bin.mkdir(mode=0o700)
        for tool in ('happyranch', 'curl', 'sleep', 'rm', 'rmdir'):
            executable = self.bin / tool
            executable.write_text(f'#!{sys.executable}\n' + _TOOL_STUB)
            executable.chmod(0o700)
        self.env = {
            'PATH': f'{self.bin}:{Path(sys.executable).parent}:/usr/bin:/bin',
            'CONTROL_ROOT': str(root), 'CONTROL_PLANS': str(self.paths.plans),
            'LANG': 'C.UTF-8',
        }
        for variable in ('HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME',
                         'XDG_STATE_HOME', 'XDG_RUNTIME_DIR', 'TMPDIR', 'HAPPYRANCH_DAEMON_HOME'):
            directory = root / variable
            directory.mkdir(mode=0o700)
            self.env[variable] = str(directory)
        daemon = Path(self.env['HAPPYRANCH_DAEMON_HOME'])
        (daemon / 'daemon.port').write_text('1')
        (daemon / 'daemon.token').write_text('synthetic-not-a-credential')
        self.script = getattr(containment, f'build_{family}_plan')(self.paths.plans)
        self.unrelated = self.paths.plans / 'unrelated-sentinel'
        self.unrelated.write_text('unrelated')
        self.unrelated_inode = self.unrelated.stat().st_ino

    def run(self, agent: str = 'engineering_head', **controls: str) -> subprocess.CompletedProcess:
        result = subprocess.run(
            ['bash', '-c', self.script, 'plan', 'TASK-042', "session's space", agent, 'test', 'reply'],
            cwd=self.root, env=self.env | controls, capture_output=True, text=True, timeout=15,
        )
        assert self.unrelated.read_text() == 'unrelated'
        assert self.unrelated.stat().st_ino == self.unrelated_inode
        return result

    def calls(self) -> list[dict]:
        receipt = self.root / 'calls.jsonl'
        return [json.loads(line) for line in receipt.read_text().splitlines()] if receipt.exists() else []

    def assert_paths(self) -> None:
        for call in self.calls():
            assert Path(call['path']).is_relative_to(self.paths.plans)
            assert stat.S_ISREG(call['mode'])
            assert call['mode'] & 0o777 == 0o600
            assert call['parent_mode'] & 0o777 == 0o700
            assert call['uid'] == os.getuid()
        assert not list(self.paths.plans.glob('invocation-*'))


@pytest.mark.parametrize('family,stages,jobs', [
    ('threads', ['engineering_head'], 0),
    ('thread_reply', ['payment_agent'], 0),
    ('review_required', ['engineering_head'], 1),
    ('persistent', ['engineering_head'], 1),
    ('blocked_multi', ['engineering_head', 'engineering_head'], 2),
    ('blocked_autonomous', ['engineering_head', 'engineering_head'], 1),
    ('content', ['content_manager', 'content_writer', 'content_manager', 'content_qa', 'content_manager'], 0),
])
def test_actual_plans_duplicate_ids_private_roots_and_submitted_jobs(
    tmp_path: Path, family: str, stages: list[str], jobs: int,
) -> None:
    roots = [PlanRun(tmp_path / f"run {n}'s", family) for n in range(2)]
    for plan in roots:
        for agent in stages:
            result = plan.run(agent)
            assert result.returncode == 0, result.stderr
        calls = plan.calls()
        submits = [c for c in calls if c['args'][:2] == ['jobs', 'submit']]
        assert len(submits) == jobs
        for submitted in submits:
            payload = submitted['payload']
            assert payload['task_id'] == 'TASK-042'
            assert payload['session_id'] == "session's space"
            assert payload['interpreter'] == 'bash'
            assert payload['review_required'] is (family in ('review_required', 'blocked_multi'))
            if family != 'review_required':
                assert payload['persistent'] is (family == 'persistent')
            job = subprocess.run(['bash', '-c', payload['script']], cwd=plan.root,
                                 env=plan.env, capture_output=True, text=True, timeout=5)
            assert job.returncode == 0, job.stderr
            if family == 'review_required':
                sentinel = plan.paths.plans / 'happyranch-job-e2e-sentinel'
                info = sentinel.stat()
                assert sentinel.read_bytes() == b''
                assert info.st_uid == os.getuid() and info.st_mode & 0o777 == 0o600
            else:
                expected = {'persistent': 'starting', 'blocked_autonomous': 'autonomous-job-ran'}
                assert job.stdout.strip() == expected.get(family, 'job-a-ran' if submitted == submits[0] else 'job-b-ran')
        completions = [c['payload'] for c in calls if c['tool'] == 'curl' or c['args'][0] == 'report-completion']
        if family.startswith('blocked_'):
            assert completions[0] == {
                'session_id': "session's space", 'agent': 'engineering_head', 'status': 'blocked',
                'confidence': 0, 'risks_flagged': [], 'dependencies': [], 'suggested_reviewer_focus': [],
                'output_summary': 'Waiting for JOB-001 and JOB-002 before proceeding.' if jobs == 2 else 'Waiting for JOB-001 to finish before proceeding.',
                'waiting_on_job_ids': ['JOB-001', 'JOB-002'] if jobs == 2 else ['JOB-001'],
            }
            assert completions[1]['status'] == 'completed'
            assert json.loads(completions[1]['summary'])['action'] == 'done'
            assert (plan.paths.plans / 'invocation_counter').read_text().strip() == '2'
            if jobs == 2:
                assert [(plan.paths.plans / f'job_{suffix}.id').read_text().strip() for suffix in ('a', 'b')] == ['JOB-001', 'JOB-002']
                assert submits[0]['path'] != submits[1]['path']
        elif family == 'content':
            assert [p.get('decision', {}).get('action') for p in completions] == ['delegate', None, 'delegate', None, 'done']
            assert completions[0]['decision']['agent'] == 'content_writer'
            assert completions[2]['decision']['agent'] == 'content_qa'
            assert completions[3]['summary'] == 'VERDICT: PASS - content is accurate'
            assert (plan.paths.plans / 'cm_step.txt').read_text().strip() == '3'
        elif family == 'thread_reply':
            assert calls[0]['payload'] == {'thread_id': 'TASK-042', 'invocation_token': "session's space", 'speaker': 'payment_agent', 'body_markdown': 'got it', 'in_response_to_seq': 1}
        elif family == 'threads':
            assert calls[0]['payload'] == {'composer': 'engineering_head', 'subject': 'int test loop in', 'recipients': ['payment_agent'], 'body_markdown': 'looping payment_agent in'}
            assert completions[0]['summary'] == 'composed thread'
        elif family == 'review_required':
            assert completions[0] == {'task_id': 'TASK-042', 'session_id': "session's space", 'agent': 'engineering_head', 'status': 'blocked', 'summary': 'Awaiting JOB-001', 'confidence': 50, 'risks_flagged': [], 'dependencies': [], 'suggested_reviewer_focus': []}
        else:
            assert json.loads(completions[0]['summary']) == {'action': 'done', 'summary': 'loop launched, founder stopped'}
            sleeps = [json.loads(line) for line in (plan.root / 'sleeps.jsonl').read_text().splitlines()]
            assert ['0.1'] in sleeps and ['60'] in sleeps
        plan.assert_paths()
    assert {c['path'] for c in roots[0].calls()}.isdisjoint(c['path'] for c in roots[1].calls())


@pytest.mark.parametrize('family,agent,callback', [
    ('threads', 'engineering_head', 1), ('threads', 'engineering_head', 2),
    ('thread_reply', 'payment_agent', 1),
    ('review_required', 'engineering_head', 1), ('review_required', 'engineering_head', 2),
    ('persistent', 'engineering_head', 1), ('persistent', 'engineering_head', 2),
    ('blocked_multi', 'engineering_head', 1), ('blocked_multi', 'engineering_head', 2), ('blocked_multi', 'engineering_head', 3),
    ('blocked_autonomous', 'engineering_head', 1), ('blocked_autonomous', 'engineering_head', 2),
    ('content', 'content_manager', 1), ('content', 'content_writer', 1), ('content', 'content_qa', 1),
])
@pytest.mark.parametrize('control,exit_code', [('FAIL_CALLBACK', 37), ('ABORT_CALLBACK', 143)])
def test_actual_plan_callback_failure_and_abort_cleanup(
    tmp_path: Path, family: str, agent: str, callback: int, control: str, exit_code: int,
) -> None:
    plan = PlanRun(tmp_path / 'run', family)
    result = plan.run(agent, **{control: str(callback)})
    assert result.returncode == exit_code, result.stderr
    assert len(plan.calls()) == callback
    plan.assert_paths()


@pytest.mark.parametrize('control,primary', [({}, 0), ({'FAIL_CALLBACK': '2'}, 37), ({'ABORT_CALLBACK': '2'}, 143)])
def test_plan_preserves_primary_and_every_cleanup_error(tmp_path: Path, control: dict, primary: int) -> None:
    plan = PlanRun(tmp_path / 'run', 'review_required')
    result = plan.run(FAIL_CLEANUP='1', **control)
    assert result.returncode == (primary or 1)
    assert result.stderr.count(f'primary={primary}') == 4  # submit, log, completion, directory
    assert result.stderr.count('exit=51') == 3
    assert result.stderr.count('exit=52') == 1
    cleanup = [json.loads(line) for line in (plan.root / 'cleanup.jsonl').read_text().splitlines()]
    assert [c['tool'] for c in cleanup] == ['rm', 'rm', 'rm', 'rmdir']
    assert all(Path(c['args'][-1]).is_relative_to(plan.paths.plans) for c in cleanup)


@pytest.mark.parametrize('kind', ['file', 'symlink', 'old'])
def test_submitted_review_job_rejects_foreign_or_old_sentinel(tmp_path: Path, kind: str) -> None:
    plan = PlanRun(tmp_path / 'run', 'review_required')
    assert plan.run().returncode == 0
    script = plan.calls()[0]['payload']['script']
    sentinel = plan.paths.plans / 'happyranch-job-e2e-sentinel'
    foreign = plan.root / 'foreign'
    foreign.write_text('foreign contents')
    if kind == 'symlink':
        sentinel.symlink_to(foreign)
    elif kind == 'file':
        sentinel.write_text('foreign file')
    else:
        assert subprocess.run(['bash', '-c', script], env=plan.env).returncode == 0
    before = (sentinel.lstat(), sentinel.read_bytes(), foreign.stat(), foreign.read_bytes())
    result = subprocess.run(['bash', '-c', script], env=plan.env, capture_output=True)
    assert result.returncode != 0
    assert before == (sentinel.lstat(), sentinel.read_bytes(), foreign.stat(), foreign.read_bytes())


@pytest.mark.parametrize('name', ['job-submitted.txt', 'founder-acted.txt'])
@pytest.mark.parametrize('kind', ['file', 'symlink'])
def test_persistent_plan_rejects_old_readiness_before_submission(tmp_path: Path, name: str, kind: str) -> None:
    plan = PlanRun(tmp_path / 'run', 'persistent')
    foreign = plan.root / 'foreign'
    foreign.write_text('foreign')
    sentinel = plan.paths.plans / name
    if kind == 'symlink':
        sentinel.symlink_to(foreign)
    else:
        sentinel.write_text('old')
    before = sentinel.lstat().st_ino, sentinel.read_bytes(), foreign.stat().st_ino
    assert plan.run().returncode != 0
    assert not plan.calls()
    assert before == (sentinel.lstat().st_ino, sentinel.read_bytes(), foreign.stat().st_ino)
    assert foreign.read_text() == 'foreign'
    plan.assert_paths()


@pytest.mark.parametrize('kind', ['root-symlink', 'root-dangling', 'ancestor-dangling', 'deep-ancestor', 'root-file'])
def test_private_paths_negative_ancestry_preserves_foreign(tmp_path: Path, kind: str) -> None:
    foreign = tmp_path / 'foreign'
    foreign.mkdir()
    sentinel = foreign / 'sentinel'
    sentinel.write_text('foreign')
    root = tmp_path / 'candidate'
    if kind == 'root-file':
        root.write_text('foreign root')
    elif kind.startswith('root-'):
        root.symlink_to(foreign if kind == 'root-symlink' else tmp_path / 'absent')
    elif kind == 'ancestor-dangling':
        alias = tmp_path / 'alias'
        alias.symlink_to(tmp_path / 'absent')
        root = alias / 'candidate'
    else:
        deep = tmp_path / 'one' / 'two' / 'three'
        deep.mkdir(parents=True)
        alias = deep / 'alias'
        alias.symlink_to(foreign)
        root = alias / 'candidate'
    before = sentinel.stat().st_ino, sentinel.read_text()
    with pytest.raises(OSError):
        prepare_private_test_paths(root)
    assert before == (sentinel.stat().st_ino, sentinel.read_text())
    assert list(foreign.iterdir()) == [sentinel]


@pytest.mark.parametrize('root', [Path('relative'), Path('/'), Path('/one/../candidate')])
def test_private_paths_reject_invalid_root_before_open(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail('invalid root reached filesystem open')
    monkeypatch.setattr(os, 'open', forbidden)
    with pytest.raises(ValueError):
        prepare_private_test_paths(root)


@pytest.mark.parametrize('seam', ['root-open', 'plans-open', 'artifacts-open', 'fstat'])
def test_private_paths_cleanup_each_acquisition_gap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seam: str) -> None:
    real_open, real_fstat = os.open, os.fstat
    opened = []
    def controlled_open(path: str, *args: object, **kwargs: object) -> int:
        if path == {'root-open': 'candidate', 'plans-open': 'plans', 'artifacts-open': 'artifacts'}.get(seam):
            raise OSError('primary acquisition gap')
        fd = real_open(path, *args, **kwargs)
        opened.append(fd)
        return fd
    def controlled_stat(fd: int) -> os.stat_result:
        if seam == 'fstat':
            raise OSError('primary acquisition gap')
        return real_fstat(fd)
    monkeypatch.setattr(os, 'open', controlled_open)
    monkeypatch.setattr(os, 'fstat', controlled_stat)
    with pytest.raises(OSError, match='primary acquisition gap'):
        prepare_private_test_paths(tmp_path / 'candidate')
    assert not (tmp_path / 'candidate').exists()
    for fd in opened:
        with pytest.raises(OSError):
            real_fstat(fd)


def test_private_paths_preserve_primary_all_removal_and_close_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_mkdir, real_close = os.mkdir, os.close
    closed = []
    removed = []
    def mkdir(path: str, *args: object, **kwargs: object) -> None:
        if path == 'artifacts':
            raise OSError('primary artifacts failure')
        real_mkdir(path, *args, **kwargs)
    def rmdir(path: str, *args: object, **kwargs: object) -> None:
        removed.append(path)
        raise OSError(f'secondary remove {path}')
    def close(fd: int) -> None:
        real_close(fd)
        closed.append(fd)
        raise OSError(f'secondary close {fd}')
    monkeypatch.setattr(os, 'mkdir', mkdir)
    monkeypatch.setattr(os, 'rmdir', rmdir)
    monkeypatch.setattr(os, 'close', close)
    with pytest.raises(ExceptionGroup) as caught:
        prepare_private_test_paths(tmp_path / 'candidate')
    errors = caught.value.exceptions
    assert str(errors[0]) == 'primary artifacts failure'
    assert [str(e) for e in errors[1:3]] == ['secondary remove plans', 'secondary remove candidate']
    assert len(errors) == 3 + len(closed)
    assert removed == ['plans', 'candidate']
    assert [str(e) for e in errors[3:]] == [f'secondary close {fd}' for fd in closed]
    assert (tmp_path / 'candidate' / 'plans').is_dir()  # residue stays visible
    assert not (tmp_path / 'candidate' / 'artifacts').exists()


@pytest.mark.parametrize('family,agent', [('blocked_multi', 'engineering_head'), ('blocked_autonomous', 'engineering_head'), ('content', 'content_manager')])
@pytest.mark.parametrize('control,exit_code', [('FAIL_CALLBACK', 37), ('ABORT_CALLBACK', 143)])
def test_resumed_plan_cleanup_paths(tmp_path: Path, family: str, agent: str, control: str, exit_code: int) -> None:
    plan = PlanRun(tmp_path / 'run', family)
    assert plan.run(agent).returncode == 0
    prior = len(plan.calls())
    result = plan.run(agent, **{control: str(prior + 1)})
    assert result.returncode == exit_code
    assert len(plan.calls()) == prior + 1
    plan.assert_paths()


@pytest.mark.parametrize("missing", ["NODE_NAME", "ADMITTED_ACCOUNT", "ADMITTED_START", "BUILD_NUMBER"])
@pytest.mark.parametrize("kind", ["file", "symlink"])
def test_pipeline_early_exit_has_no_acquisition_and_preserves_foreign(tmp_path, missing, kind):
    env, trace = _pipeline_tools(tmp_path)
    foreign = tmp_path / "foreign"
    foreign.write_text("FOREIGN")
    root = Path(env["WORKSPACE"]) / "thr211-1"
    if kind == "file":
        root.write_text("FOREIGN")
    else:
        root.symlink_to(foreign)
    before = (foreign.stat().st_ino, foreign.read_bytes(), root.lstat().st_ino)
    del env[missing]
    result = subprocess.run(["/bin/bash", "-p", "-c", _controlled_prepare_shell()], env=env, capture_output=True, timeout=5)
    assert _shell_exit(result) != 0
    assert b"THR211_ACQUIRED=" not in result.stdout
    assert not trace.exists()
    assert before == (foreign.stat().st_ino, foreign.read_bytes(), root.lstat().st_ino)


@pytest.mark.parametrize("change", ["none", "root", "artifacts", "ancestor", "file", "hardlink", "directory", "nonce", "stale", "root-directory"])
def test_actual_pipeline_writer_rechecks_acquisition_and_never_follows(tmp_path, change):
    env, trace = _pipeline_tools(tmp_path, failure="clone")
    result = subprocess.run(["/bin/bash", "-p", "-c", _controlled_prepare_shell()], env=env, capture_output=True, timeout=5)
    assert _shell_exit(result) == 37
    acquisition = _acquired(result)
    root = Path(acquisition["root"])
    foreign = tmp_path / "foreign"
    foreign.mkdir(mode=0o700)
    (foreign / "artifacts").mkdir(mode=0o700)
    marker = foreign / "artifacts/pipeline.json"
    marker.write_text("FOREIGN")
    before = (marker.stat().st_ino, marker.read_bytes())
    if change in ("root", "artifacts", "ancestor"):
        target = {"root": root, "artifacts": root / "artifacts", "ancestor": root.parent}[change]
        target.rename(target.with_name(target.name + "-owned"))
        target.symlink_to(foreign, target_is_directory=True)
    elif change == "file":
        (root / "artifacts/pipeline.json").symlink_to(marker)
    elif change == "hardlink":
        os.link(marker, root / "artifacts/pipeline.json")
    elif change == "directory":
        (root / "artifacts/pipeline.json").mkdir()
    elif change == "nonce":
        acquisition["nonce"] = "stale-nonce"
    elif change == "stale":
        acquisition["ancestry"][-1][1] += 1
    elif change == "root-directory":
        root.rename(root.with_name("owned-root"))
        root.mkdir(mode=0o700)
        for leaf in ("home", "xdg-config", "xdg-cache", "xdg-state", "xdg-runtime", "tmp", "uv-cache", "venv", "daemon-home", "artifacts"):
            (root / leaf).mkdir(mode=0o700)
    publish_env = dict(env, PUBLICATION_PYTHON=env["ADMITTED_PYTHON"], PUBLICATION_ROOT=str(root),
                       PUBLICATION_ACQUIRED=json.dumps(acquisition), PUBLICATION_RECEIPT='{"pytest_exit":null}')
    written = subprocess.run(["/bin/bash", "-p", "-c", _pipeline_shell("publishShell")], env=publish_env, capture_output=True, timeout=5)
    if change == "none":
        assert written.returncode == 0, written.stderr
        assert _acquired(written)["published"] is True
        assert json.loads((root / "artifacts/pipeline.json").read_text()) == {"pytest_exit": None}
    else:
        assert written.returncode != 0 or not _acquired(written).get("published")
    assert before == (marker.stat().st_ino, marker.read_bytes())


@pytest.mark.parametrize("changed", ["root", "artifacts"])
@pytest.mark.parametrize("terminal,expected", [("exit 37", 37), ('kill -TERM "$$"', 143)])
def test_setup_replacement_cannot_redirect_supervisor_receipt(tmp_path, changed, terminal, expected):
    import shlex
    env, trace = _pipeline_tools(tmp_path, failure="clone")
    root = Path(env["WORKSPACE"]) / "thr211-1"
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    marker = foreign / "shell-result.json"
    marker.write_text("FOREIGN")
    (foreign / "artifacts").mkdir()
    second = foreign / "artifacts/shell-result.json"
    second.write_text("FOREIGN")
    before = [(p.stat().st_ino, p.read_bytes()) for p in (marker, second)]
    target = root if changed == "root" else root / "artifacts"
    renamed = target.with_name(target.name + "-owned")
    git = Path(env["ADMITTED_GIT"])
    git.write_text("#!/bin/bash -p\nset -eu\n" +
                   f"mv {shlex.quote(str(target))} {shlex.quote(str(renamed))}\n" +
                   f"ln -s {shlex.quote(str(foreign))} {shlex.quote(str(target))}\n" + terminal + "\n")
    result = subprocess.run(["/bin/bash", "-p", "-c", _controlled_prepare_shell()], env=env, capture_output=True, timeout=5)
    assert _shell_exit(result) == expected
    acquired = _acquired(result)
    assert acquired["exit"] == expected
    assert before == [(p.stat().st_ino, p.read_bytes()) for p in (marker, second)]
    owned_artifacts = renamed / "artifacts" if changed == "root" else renamed
    assert json.loads((owned_artifacts / "shell-result.json").read_text())["primary_exit"] == expected


def test_publication_retains_writer_and_every_descriptor_close_error(tmp_path):
    import shlex
    env, trace = _pipeline_tools(tmp_path, failure="clone")
    setup = subprocess.run(["/bin/bash", "-p", "-c", _controlled_prepare_shell()], env=env, capture_output=True, timeout=5)
    assert _shell_exit(setup) == 37
    acquired = _acquired(setup)
    target = Path(acquired["root"]) / "artifacts/pipeline.json"
    target.write_text("EXISTING")
    words = shlex.split(_pipeline_shell("publishShell"))
    code = words[words.index("-c") + 1]
    injected = 'import os\nclose = os.close\ndef fail_close(fd):\n close(fd)\n raise OSError("injected close")\nos.close = fail_close\n'
    written = subprocess.run([sys.executable, "-I", "-c", injected + code, "publish", acquired["root"],
                              env["PUBLICATION_NONCE"], json.dumps(acquired), '{"primary_exit":37}'],
                             env=env, capture_output=True, timeout=5)
    assert written.returncode == 0
    errors = _acquired(written)["errors"]
    assert errors[0].startswith("publication:FileExistsError:")
    assert len([error for error in errors if error.startswith("close:OSError:")]) == len(acquired["ancestry"]) + 1
    assert target.read_text() == "EXISTING"


@pytest.mark.parametrize('failure', ['absent', 'stale', 'request', 'source', 'evaluated', 'phase'])
def test_operator_lease_refuses_before_tools(tmp_path, failure):
    env, trace = _pipeline_tools(tmp_path)
    ready = Path(env['ADMITTED_READY'])
    if failure == 'absent':
        ready.unlink()
    else:
        fields = ready.read_text().split()
        index = {'stale': 6, 'request': 0, 'source': 1, 'evaluated': 7, 'phase': 8}[failure]
        fields[index] = '1000000000' if failure == 'stale' else 'invalid'
        ready.write_text(' '.join(fields) + '\n')
    result = subprocess.run(['/bin/bash', '-p', '-c', _controlled_prepare_shell()], env=env, capture_output=True, timeout=5)
    assert _shell_exit(result) == 64
    assert not trace.exists()


def test_actual_lease_rejects_target_writable_control(tmp_path):
    import socket
    env, trace = _pipeline_tools(tmp_path)
    env.update(ADMITTED_UID=str(os.getuid()), ADMITTED_HOST=socket.gethostname())
    result = subprocess.run(['/bin/bash', '-p', '-c', _pipeline_shell()], env=env, capture_output=True, timeout=5)
    assert _shell_exit(result) == 64
    assert not trace.exists()


@pytest.mark.parametrize('code,junit,expected', [(0, True, 0), (3, True, 3), (0, False, 74), (7, False, 7)])
def test_actual_workload_command_and_private_export(tmp_path, monkeypatch, code, junit, expected):
    root = tmp_path / 'private'
    root.mkdir(mode=0o700)
    source = root / 'source'
    source.mkdir(mode=0o700)
    (root / 'artifacts').mkdir(mode=0o700)
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs['cwd']))
        kwargs['stdout'].write('controlled workload log\n')
        if junit:
            (source / 'artifacts/integration.xml').write_text('<testsuite tests="1"/>')
        return subprocess.CompletedProcess(argv, code)
    monkeypatch.setattr(containment.subprocess, 'run', run)
    assert containment.run_pipeline_workload(str(source), str(root)) == expected
    assert calls == [(['uv', 'run', '--frozen', 'pytest', 'tests/integration/', '-v', '-m', 'integration', '--junitxml=artifacts/integration.xml'], source)]
    receipt = json.loads((root / 'artifacts/workload-result.json').read_text())
    assert receipt['pytest_exit'] == code and receipt['cleanup'] == 'UNKNOWN'
    assert receipt['export'] == ('EXPORTED' if junit else 'INCOMPLETE')
    assert (root / 'artifacts/integration.xml').exists() is junit


def test_actual_emitted_workload_environment_registers_async_plugin_and_archives_junit(tmp_path):
    """Run a separate tiny async probe through the emitted shell's real environment.

    A controlled uv shim checks the selected argv and runs ONLY probe_async.py.
    The selected integration directory does not exist in this test's source.
    """
    import shlex
    import shutil
    root = tmp_path / 'private'
    root.mkdir(mode=0o700)
    for leaf in ('home', 'xdg-config', 'xdg-cache', 'xdg-state', 'xdg-runtime', 'tmp', 'uv-cache', 'venv', 'daemon-home', 'bin', 'source', 'artifacts'):
        (root / leaf).mkdir(mode=0o700)
    (root / 'venv/env/bin').mkdir(mode=0o700, parents=True)
    python = root / 'venv/env/bin/python'
    python.write_text('#!/bin/bash -p\nexec ' + shlex.quote(sys.executable) + ' "$@"\n')
    python.chmod(0o700)
    source = root / 'source'
    (source / 'tests').mkdir(mode=0o700)
    (source / 'tests/__init__.py').write_text('')
    shutil.copyfile(Path(containment.__file__), source / 'tests/thr211_containment.py')
    (source / 'pytest.ini').write_text('[pytest]\nasyncio_mode = auto\n')
    (source / 'probe_async.py').write_text('''import asyncio, json, os, sys
import pytest_asyncio.plugin
async def test_real_async():
    await asyncio.sleep(0)
    assert os.environ['PYTEST_PLUGINS'] == 'pytest_asyncio.plugin'
    assert os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] == '1'
    assert 'credential-canary' not in str(dict(os.environ))
    print(json.dumps(dict(python=sys.executable, version=sys.version, plugin=pytest_asyncio.plugin.__file__, home=os.environ['HOME'])))
''')
    uv = root / 'bin/uv'
    uv.write_text('#!/bin/bash -p\nset -eu\n' +
                  '[[ "$*" == "run --frozen pytest tests/integration/ -v -m integration --junitxml=artifacts/integration.xml" ]]\n' +
                  'exec ' + shlex.quote(sys.executable) + ' -m pytest probe_async.py -v -s --junitxml=artifacts/integration.xml\n')
    uv.chmod(0o700)
    acquired = dict(root=str(root), nonce='probe', ancestry=[])
    for path in reversed((root / 'artifacts', *(root / 'artifacts').parents)):
        if path != Path('/'):
            info = path.stat()
            acquired['ancestry'].append([info.st_dev, info.st_ino])
    env = dict(PUBLICATION_ROOT=str(root), PUBLICATION_ACQUIRED=json.dumps(acquired), PUBLICATION_NONCE='probe',
               PUBLICATION_PYTHON=sys.executable, HTTPS_PROXY='credential-canary', PYTHONPATH='credential-canary', BASH_ENV='/absent')
    result = subprocess.run(['/bin/bash', '-p', '-c', _pipeline_shell('workloadShell')], env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert 'THR211_WORKLOAD_EXIT=0' in result.stdout, result.stdout + result.stderr
    receipt = json.loads((root / 'artifacts/workload-result.json').read_text())
    assert receipt['pytest_exit'] == 0 and receipt['export'] == 'EXPORTED'
    log = (root / 'artifacts/workload.log').read_text()
    assert '1 passed' in log and 'pytest_asyncio' in log and str(root / 'home') in log
    assert (root / 'artifacts/integration.xml').read_bytes() == (source / 'artifacts/integration.xml').read_bytes()
    # The actual preparation shell emits the same explicit registration.
    assert 'PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTEST_PLUGINS=pytest_asyncio.plugin' in _pipeline_shell()
    print(log)


def test_evaluated_pipeline_all_finite_outcomes(tmp_path):
    import hashlib
    jar = os.environ.get('THR211_GROOVY_JAR')
    if not jar:
        pytest.skip('evaluated Pipeline requires the task-provisioned Groovy jar')
    env, _ = _pipeline_tools(tmp_path)
    installed = {key.removeprefix('ADMITTED_'): value for key, value in env.items() if key.startswith('ADMITTED_')}
    installed['MODE'] = 'DIAGNOSTIC'
    prefix = "binding.setVariable('THR211_INSTALL', [" + ','.join(json.dumps(k)+':'+json.dumps(v) for k,v in installed.items()) + '])\n'
    evaluated = tmp_path / 'installed.groovy'
    evaluated.write_text(prefix + (Path(__file__).parents[1] / 'Jenkinsfile').read_text())
    digest = hashlib.sha256(evaluated.read_bytes()).hexdigest()
    inputs = tmp_path / 'input.json'
    inputs.write_text(json.dumps(dict(workspace=env['WORKSPACE'], evaluated=digest)))
    probe = tmp_path / 'outcomes.groovy'
    probe.write_text(PIPELINE_OUTCOMES_GROOVY)
    result = subprocess.run(['/usr/bin/java', f'-Djava.io.tmpdir={tmp_path}', '-cp', jar, 'groovy.ui.GroovyMain', str(probe), str(evaluated), str(inputs)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)['passed'] == ['normal', 'setup-failure', 'abort', 'allocation-failure', 'lost-agent', 'publication-failure', 'test-failure', 'export-failure', 'lease-loss']


PIPELINE_OUTCOMES_GROOVY = r'''
import groovy.json.JsonOutput
import groovy.json.JsonSlurperClassic
class FlowInterruptedException extends RuntimeException {}
def input = new JsonSlurperClassic().parseText(new File(args[1]).text)
def passed = []
['normal', 'setup-failure', 'abort', 'allocation-failure', 'lost-agent', 'publication-failure', 'test-failure', 'export-failure', 'lease-loss'].each { scenario ->
  def b = new Binding()
  b.setVariable('params', [REQUEST_ID:'controlled-1', MODE:'DIAGNOSTIC', SOURCE_SHA:'a'*40, PIPELINE_SHA:'b'*64, EVALUATED_SHA:input.evaluated])
  b.setVariable('env', [BUILD_NUMBER:'1'])
  b.setVariable('currentBuild', [result:null])
  b.setVariable('properties', { List x -> })
  b.setVariable('disableConcurrentBuilds', { -> [:] })
  b.setVariable('parameters', { List x -> x })
  b.setVariable('choice', { Map x -> x })
  b.setVariable('string', { Map x -> x })
  b.setVariable('parallel', { Map x -> x.execution(); x.allocation() })
  def primary = scenario == 'abort' ? new FlowInterruptedException() : new IOException(scenario)
  def calls = []
  def childEnv = [:]
  def stageName = ''
  def observed = null
  b.setVariable('node', { String x, Closure body ->
    if (scenario == 'allocation-failure') throw primary
    body()
  })
  b.setVariable('pwd', { -> input.workspace })
  b.setVariable('stage', { String x, Closure body -> stageName=x; body() })
  def budgets = []
  b.setVariable('timeout', { Map x, Closure body -> budgets.add(x.time); body() })
  b.setVariable('withEnv', { List x, Closure body ->
    def old=[:] + childEnv
    x.each { def parts=it.toString().split('=', 2); childEnv[parts[0]]=parts[1] }
    try { body() } finally { childEnv=old }
  })
  b.setVariable('sh', { Map x ->
    assert x.returnStdout
    if (childEnv.containsKey('PUBLICATION_RECEIPT')) {
      calls.add('publish')
      if (scenario == 'lost-agent') throw new IOException('agent still absent')
      return 'THR211_ACQUIRED=' + JsonOutput.toJson([published:true, nonce:childEnv.PUBLICATION_NONCE, root:childEnv.PUBLICATION_ROOT, errors:[]])
    }
    if (stageName == 'Private frozen preparation') {
      calls.add('prepare')
      def code=scenario == 'setup-failure' ? 37 : 0
      return 'THR211_ACQUIRED=' + JsonOutput.toJson([nonce:childEnv.PUBLICATION_NONCE, root:input.workspace+'/thr211-1', ancestry:[[1,2]], exit:code, errors:[]]) + '\nTHR211_EXIT='+code+'\n'
    }
    if (!childEnv.containsKey('PUBLICATION_ROOT')) {
      calls.add('lease')
      return scenario == 'lease-loss' ? 'THR211_EXIT=64\n' : 'THR211_EXIT=0\n'
    }
    calls.add('workload')
    if (scenario in ['abort','lost-agent']) throw primary
    def code=scenario == 'test-failure' ? 3 : 0
    def exported=scenario != 'export-failure'
    return 'THR211_WORKLOAD=' + JsonOutput.toJson([pytest_exit:code, export:exported?'EXPORTED':'INCOMPLETE']) + '\nTHR211_WORKLOAD_EXIT=' + (exported?code:74) + '\n'
  })
  b.setVariable('archiveArtifacts', { Map x ->
    calls.add('archive')
    assert x.followSymlinks == false && x.allowEmptyArchive == false
    if (scenario == 'publication-failure') throw new IOException('archive failed')
  })
  b.setVariable('echo', { String x -> observed=new JsonSlurperClassic().parseText(x) })
  b.setVariable('error', { String x -> throw primary })
  Throwable thrown
  try { new GroovyShell(b).evaluate(new File(args[0])) } catch(Throwable e) { thrown=e }
  if (scenario == 'normal') {
    assert thrown == null
    assert observed.result == 'TESTS_PASSED' && observed.pytest_exit == 0
    assert budgets == [15,30,5,15]
  } else {
    assert thrown.is(primary)
  }
  if (scenario in ['setup-failure','allocation-failure','lease-loss']) assert !calls.contains('workload')
  if (scenario == 'allocation-failure') assert calls == []
  else {
    assert observed.evaluated_pipeline == input.evaluated
    assert observed.cleanup == 'UNKNOWN'
  }
  if (scenario == 'abort') assert observed.result == 'ABORTED'
  if (scenario == 'test-failure') assert observed.pytest_exit == 3
  if (scenario == 'export-failure') assert observed.pytest_exit == 0 && observed.workload_exit == 74 && observed.export == 'INCOMPLETE'
  if (scenario == 'lost-agent') assert observed.pytest_exit == null && !calls.contains('archive') && observed.errors
  if (scenario == 'publication-failure') {
    assert observed.errors == ['receipt-archive:IOException', 'artifacts:IOException']
    assert b.getVariable('currentBuild').result == 'FAILURE'
  }
  passed.add(scenario)
}
println(JsonOutput.toJson([passed:passed]))
'''


def test_workload_refuses_linked_junit_without_copying_foreign_bytes(tmp_path, monkeypatch):
    root = tmp_path / 'private'
    (root / 'source').mkdir(parents=True)
    (root / 'artifacts').mkdir()
    foreign = tmp_path / 'foreign'
    foreign.write_text('DO NOT EXPORT')
    def run(argv, **kwargs):
        (root / 'source/artifacts/integration.xml').symlink_to(foreign)
        return subprocess.CompletedProcess(argv, 0)
    monkeypatch.setattr(containment.subprocess, 'run', run)
    assert containment.run_pipeline_workload(str(root / 'source'), str(root)) == 74
    assert not (root / 'artifacts/integration.xml').exists()
    assert foreign.read_text() == 'DO NOT EXPORT'
