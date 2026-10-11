"""Public bootstrap, real providers/callback CLI and independent observations."""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from oracles import Store, require
from transport import Relay


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Controller:
    def __init__(self, args: argparse.Namespace) -> None:
        self.root, self.source = args.root, args.source
        self.revision, self.deadline = args.sha, args.deadline
        self.env = dict(os.environ)
        self.python = str(self.root / "venv" / "bin" / "python")
        self.port = 0
        self.token = ""
        self.daemon = None
        self.relay = None
        self.browser = None
        self.starts: list[dict] = []
        self.holds: dict[str, tuple] = {}
        self.payloads: dict[str, dict] = {}
        self.results: dict[str, dict] = {}
        self.commands: list[dict] = []
        self.http: list[dict] = []
        self.durable: list[dict] = []
        self.phases: list[dict] = []
        self.server = socket.socket(socket.AF_UNIX)
        self.socket_path = self.root / "coord" / "provider.sock"
        self.server.bind(str(self.socket_path))
        self.server.listen(8)
        self.stores = {org: Store(self.root, org) for org in ("alpha", "beta")}

    def remaining(self, cap: float = 30) -> float:
        remaining = self.deadline - time.monotonic()
        require(remaining > 0, "action deadline exceeded")
        return min(remaining, cap)

    def poll(self, predicate: Callable[[], Any], name: str, cap: float = 30) -> Any:
        end = time.monotonic() + self.remaining(cap)
        while time.monotonic() < end:
            value = predicate()
            if value:
                return value
            time.sleep(min(0.05, max(0, end - time.monotonic())))
        raise AssertionError(f"deadline waiting for {name}")

    def api(self, method: str, path: str, body: dict | None = None, bearer: str | None = "valid") -> tuple[int, dict]:
        headers = {"Content-Type": "application/json"}
        if bearer is not None:
            headers["Authorization"] = "Bearer " + (self.token if bearer == "valid" else bearer)
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=self.remaining())
        try:
            connection.request(method, path, json.dumps(body) if body is not None else None, headers)
            response = connection.getresponse()
            data = json.loads(response.read())
            self.http.append(dict(method=method, path=path, status=response.status, response=data))
            return response.status, data
        finally:
            connection.close()

    def get(self, path: str) -> dict:
        status, body = self.api("GET", path)
        require(status == 200, "public GET success", (path, status, body))
        return body

    def cli(self, args: list[str], *, relay: bool = False) -> subprocess.CompletedProcess:
        env = dict(self.env)
        if relay:
            env["HAPPYRANCH_DAEMON_HOME"] = str(self.root / "client")
        command = [str(self.root / "bin" / "happyranch"), *args]
        begin = time.monotonic()
        result = subprocess.run(command, cwd=self.root, env=env, capture_output=True, text=True, timeout=self.remaining())
        self.commands.append(dict(argv=command, exit=result.returncode, stdout=result.stdout,
                                  stderr=result.stderr, seconds=time.monotonic() - begin))
        return result

    def wrappers(self) -> None:
        binary = self.root / "bin"
        for name in ("python", "python3"):
            (binary / name).write_text(f"#!/bin/sh\nexec {shlex.quote(self.python)} -I \"$@\"\n")
        guard = self.source / "tests/helpers/integration_stub_guard/guard.py"
        cli_hash = sha(self.source / "cli/main.py")
        program = (
            "import sys, pathlib, hashlib, runpy; "
            f"s=pathlib.Path({str(self.source)!r}); "
            f"assert hashlib.sha256((s/'cli/main.py').read_bytes()).hexdigest()=={cli_hash!r}; "
            "sys.path.insert(0,str(s)); import cli.main; "
            "assert pathlib.Path(cli.main.__file__).resolve()==s/'cli/main.py'; "
            f"g=runpy.run_path({str(guard)!r},run_name='e2e_external_witness'); "
            f"g['witness']({{'kind':'callback','source_sha':{self.revision!r},'cli_sha256':{cli_hash!r}}}); "
            "sys.argv[0]='happyranch'; cli.main.main()"
        )
        callback = binary / "happyranch"
        callback.write_text(f"#!/bin/sh\nexec {shlex.quote(self.python)} -I -c {shlex.quote(program)} \"$@\"\n")
        stubs = {}
        for provider in ("claude", "codex", "opencode"):
            target = binary / provider
            shutil.copyfile(self.source / f"tests/integration/fake_{provider}.sh", target)
            stubs[provider] = dict(path=str(target), sha256=sha(target))
        plan = self.root / "coord" / "plan.sh"
        plan.write_text(f"#!/bin/sh\nexec {shlex.quote(self.python)} -I {shlex.quote(str(self.source / 'e2e/provider.py'))} \"$@\"\n")
        for file in [*binary.iterdir(), plan]:
            file.chmod(0o700)
        Path(str(plan) + ".sha256").write_text(sha(plan))
        manifest = dict(root=str(self.root), source=str(self.source), revision=self.revision,
                        python=self.python, callback=str(callback), callback_sha256=sha(callback), stubs=stubs)
        path = self.root / "coord" / "manifest.json"
        path.write_text(json.dumps(manifest))
        path.chmod(0o600)
        self.env.update(PATH=f"{binary}:/usr/bin:/bin", E2E_SOCKET=str(self.socket_path),
                        FAKE_CLAUDE_PLAN=str(plan), HAPPYRANCH_TEST_PARENT_MANIFEST=str(path),
                        HAPPYRANCH_TEST_STUB_GUARD=str(guard), HAPPYRANCH_TEST_WITNESS_DIR=str(self.root / "witness"))
        self.bindings = {str(p): sha(p) for p in [*binary.iterdir(), plan, Path(str(plan) + ".sha256"), path]}
        interpreter = Path(self.python).resolve()
        require(Path(sys.executable).resolve() == interpreter, "controller uses bound interpreter")
        self.bindings[str(interpreter)] = sha(interpreter)
        self.durable.append(dict(executable_manifest=manifest, wrappers=self.bindings))
        self.durable.append(dict(python_executable=sys.executable, python_real=str(interpreter), python_version=sys.version))

    def validate_bindings(self) -> None:
        for path, digest in self.bindings.items():
            require(sha(Path(path)) == digest, "external executable binding changed", path)
        registry = json.loads((self.root / "daemon" / "executors.json").read_text())
        require(registry == {p: str(self.root / "bin" / p) for p in ("claude", "codex", "opencode")},
                "public registry exact provider closure", registry)

    def binding_controls(self) -> None:
        # Controls only mutate owned test assets, never product files/registry.
        # Byte-exact restoration occurs in finally; no corrupt asset is launched.
        controls = []
        for name, path in (("wrapper", self.root / "bin/happyranch"),
                           ("interpreter-wrapper", self.root / "bin/python"),
                           ("stub", self.root / "bin/claude"),
                           ("plan", self.root / "coord/plan.sh")):
            original = path.read_bytes()
            try:
                path.write_bytes(original + b"\n# changed owned control\n")
                try:
                    self.validate_bindings()
                except AssertionError as exc:
                    controls.append(dict(control=name + "-changed", refusal=str(exc)))
                else:
                    raise AssertionError(f"binding validator admitted changed {name}")
            finally:
                path.write_bytes(original)
            require(path.read_bytes() == original, "byte-exact owned control restoration")
            self.validate_bindings()
            try:
                path.unlink()
                try:
                    self.validate_bindings()
                except FileNotFoundError as exc:
                    controls.append(dict(control=name + "-missing", refusal=str(exc)))
                else:
                    raise AssertionError(f"binding validator admitted missing {name}")
            finally:
                path.write_bytes(original)
                path.chmod(0o700)
            self.validate_bindings()
        # Exercise the unchanged external guard with a missing registry home.
        # This is not a direct edit of the publicly created registry.
        env = dict(self.env, HAPPYRANCH_DAEMON_HOME=str(self.root / "missing-registry"))
        command = [self.python, "-I", str(self.source / "tests/helpers/integration_stub_guard/guard.py"),
                   str(self.root / "bin/claude"), "claude", str(self.root / "coord/plan.sh"), "-p"]
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=self.remaining())
        require(result.returncode != 0 and "deterministic stub setup refused" in result.stderr,
                "missing registry explicitly refused", result)
        controls.append(dict(control="registry-missing", exit=result.returncode, stderr=result.stderr))
        self.durable.append(dict(binding_controls=controls))

    def bootstrap(self) -> None:
        self.wrappers()
        program = f"import sys,runpy;sys.path.insert(0,{str(self.source)!r});runpy.run_module('runtime.daemon',run_name='__main__')"
        self.daemon_log = (self.root / "raw" / "daemon.txt").open("w")
        self.daemon = subprocess.Popen([self.python, "-I", "-c", program], cwd=self.root,
                                       env=self.env, stdout=self.daemon_log, stderr=subprocess.STDOUT, start_new_session=True)
        def ready() -> bool:
            require(self.daemon.poll() is None, "daemon died during bootstrap")
            home = self.root / "daemon"
            if not all((home / f"daemon.{name}").exists() for name in ("port", "token", "pid")):
                return False
            require(int((home / "daemon.pid").read_text()) == self.daemon.pid, "new daemon PID")
            self.port = int((home / "daemon.port").read_text())
            self.token = (home / "daemon.token").read_text().strip()
            try:
                return self.get("/api/v1/health")["status"] == "ok"
            except ConnectionError:
                return False
        self.poll(ready, "owned daemon readiness")
        (self.root / "ports.json").write_text(json.dumps([self.port]))
        result = self.cli(["init", str(self.root / "runtime")])
        require(result.returncode == 0, "public runtime init", result.stdout)
        for provider in ("claude", "codex", "opencode"):
            result = self.cli(["executor-binaries", "register", provider, "--path", str(self.root / "bin" / provider)])
            require(result.returncode == 0, "public provider registration", result.stdout)
        for org in ("alpha", "beta"):
            result = self.cli(["orgs", "init", org, "--from", str(self.source / "e2e/template")])
            require(result.returncode == 0, "public org creation", result.stdout)
            result = self.cli(["init-agent", "--org", org])
            require(result.returncode == 0 and "Done." in result.stdout and "error" not in result.stdout.lower(),
                    "init-agent all_done without error", result.stdout)
            self.stores[org].ordinary()
        orgs = self.get("/api/v1/orgs")["orgs"]
        require({row["slug"] for row in orgs} == {"alpha", "beta"}, "exact publicly loaded org set", orgs)
        self.validate_bindings()
        self.binding_controls()
        health = self.get("/api/v1/health")["host_sessions"]
        require(health["wired"] and health["backend"]["healthy"] and health["backend"]["name"] != "passthrough",
                "production-selected containment capability required", health)
        capabilities = health["backend"]["capabilities"]
        require(any(capabilities.get(key) in {"guaranteed", "best_effort"}
                    for key in ("kills_tree_guaranteed", "kills_tree_best_effort")),
                "real tree cleanup capability required", health)
        require(health["admission"]["cap"] >= 2, "two concurrent colliding sessions supported", health)
        self.durable.append(dict(health=health))
        self.relay = Relay(self.port, self.prove_committed)
        (self.root / "ports.json").write_text(json.dumps([self.port, self.relay.port]))
        (self.root / "client" / "daemon.port").write_text(str(self.relay.port))
        token = self.root / "client" / "daemon.token"
        token.write_text(self.token)
        token.chmod(0o600)

    def create(self, org: str, brief: str) -> str:
        status, body = self.api("POST", f"/api/v1/orgs/{org}/tasks", {"team": "engineering", "brief": brief})
        require(status == 200 and set(body) == {"task_id", "team", "assigned_agent"}, "public creation projection", (status, body))
        require(body["assigned_agent"] == "case_manager" and body["team"] == "engineering", "public owner", body)
        return body["task_id"]

    def start(self, org: str, task: str | None, agent: str) -> dict:
        self.validate_bindings()
        self.server.settimeout(self.remaining())
        connection, _ = self.server.accept()
        connection.settimeout(self.remaining())
        stream = connection.makefile("rwb")
        row = json.loads(stream.readline(4096))
        require(row["org"] == org and row["agent"] == agent and (task is None or row["task"] == task),
                "exact expected actual launch", row)
        require(row["session"] not in self.holds and row["session"] not in {s["session"] for s in self.starts}, "unique issued session", row)
        import struct
        peer_pid, peer_uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        require(peer_pid == row["pid"] and peer_uid == os.getuid(), "actual owned socket peer", row)
        from owned import identity
        row["pid_start"] = identity(peer_pid)
        row["pgid"] = os.getpgid(peer_pid)
        require(row["pid_start"] is not None, "live provider identity")
        env = Path(f"/proc/{peer_pid}/environ").read_bytes().split(b"\0")
        require(f"E2E_ROOT={self.root}".encode() in env, "provider run ownership")
        self.starts.append(row)
        temp = self.root / "owned.tmp"
        temp.write_text(json.dumps([s["pid"] for s in self.starts]))
        temp.replace(self.root / "owned.json")
        self.holds[row["session"]] = (connection, stream)
        public = self.get(f"/api/v1/orgs/{org}/tasks/{row['task']}")["task"]
        require(public["current_session_id"] == row["session"], "issued session agrees with public live binding", public)
        self.stores[org].ordinary()
        return row

    def payload(self, start: dict, summary: str, decision: dict | None = None) -> dict:
        row = dict(task_id=start["task"], session_id=start["session"], agent=start["agent"],
                   status="completed", confidence=90, summary=summary)
        if decision is not None:
            row["decision"] = decision
        return row

    def callback(self, org: str, payload: dict, status: int = 200, detail: dict | None = None, *, lost: bool = False) -> dict:
        path = self.root / "coord" / (hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest() + ".json")
        if not path.exists():
            path.write_text(json.dumps(payload, sort_keys=True))
        self.payloads[payload["session_id"]] = payload if status == 200 else self.payloads.get(payload["session_id"], payload)
        offset = len(self.relay.records)
        result = self.cli(["report-completion", "--org", org, "--from-file", str(path)], relay=True)
        require(self.relay.error is None, "relay proof/transport error", self.relay.error)
        records = self.relay.records[offset:]
        callback = [r for r in records if r["path"] == f"/api/v1/orgs/{org}/tasks/{payload['task_id']}/completion"]
        require(len(callback) == 1, "one real HTTP callback per CLI invocation", records)
        record = callback[0]
        require(record["status"] == status, "exact callback HTTP status", record)
        require(record["response"] == ({"ok": True} if status == 200 else {"detail": detail}), "exact callback response", record)
        require(result.returncode != 0 if lost else result.returncode == (0 if status == 200 else 1),
                "actual CLI exit agrees with transport", result.returncode)
        require(record["dropped"] == lost, "actual response delivery/loss", record)
        return record

    def prove_committed(self, wire: dict) -> None:
        require(wire["session_id"] in self.holds, "loss requires live held provider")
        start = next(s for s in self.starts if s["session"] == wire["session_id"])
        from owned import identity
        require(identity(start["pid"]) == start["pid_start"], "provider still alive before response drop")
        self.stores[start["org"]].result(self.payloads[wire["session_id"]])

    def release(self, start: dict) -> None:
        connection, stream = self.holds.pop(start["session"])
        stream.write(b'{"action":"exit"}\n')
        stream.flush()
        require(json.loads(stream.readline()) == {"exiting": True}, "provider exit acknowledgement")
        stream.close()
        connection.close()

    def finish(self, org: str, task: str) -> dict:
        begin = time.monotonic()
        def terminal() -> dict | None:
            body = self.get(f"/api/v1/orgs/{org}/tasks/{task}")
            require(body["task"]["status"] not in {"failed", "escalated", "cancelled"}, "unexpected terminal task", body)
            return body if body["task"]["status"] == "completed" else None
        body = self.poll(terminal, "natural terminal completion")
        self.get(f"/api/v1/orgs/{org}/tasks/{task}/recall?tree=true")
        self.phases.append(dict(name="settle", org=org, task=task, seconds=time.monotonic() - begin))
        return body

    def mark(self, variant: str, evidence: dict | None = None) -> None:
        require(variant not in self.results, "duplicate variant result", variant)
        self.results[variant] = dict(status="PASS", evidence=evidence or {}, at=time.monotonic())

    def close(self) -> None:
        failures = []
        if self.browser:
            try:
                self.browser.close()
            except Exception as exc:
                failures.append(repr(exc))
        for connection, stream in self.holds.values():
            stream.close()
            connection.close()
        self.server.close()
        self.socket_path.unlink(missing_ok=True)
        if self.relay:
            self.relay.close()
        if self.daemon and self.daemon.poll() is None:
            self.daemon.send_signal(signal.SIGTERM)
            try:
                self.daemon.wait(timeout=20)
            except subprocess.TimeoutExpired:
                pass  # Outer watcher owns verified-PID escalation and failure receipt.
        if hasattr(self, "daemon_log"):
            self.daemon_log.close()
        require(not failures, "browser cleanup/evidence failed", failures)

    def evidence(self) -> None:
        rows = dict(variants=self.results, starts=self.starts, commands=self.commands,
                    http=self.http, durable=self.durable, relay=self.relay.records if self.relay else [])
        raw = json.dumps(rows, indent=2)
        if self.token:
            raw = raw.replace(self.token, "[REDACTED]")
        (self.root / "raw" / "observations.json").write_text(raw)
        (self.root / "scenario.json").write_text(json.dumps(dict(variants=self.results, scenario_phases=self.phases)))


def cancellation_hold(controller: Controller) -> None:
    """Hold a real callback-completed provider for external launcher cancellation."""
    from browser import Browser
    from owned import identity
    controller.browser = Browser(controller)
    task = controller.create("alpha", "E2E owned launcher cancellation")
    start = controller.start("alpha", task, "case_manager")
    payload = controller.payload(start, "CANCELLATION-HOLD", {"action": "done", "summary": "CANCELLATION-HOLD"})
    callback = controller.callback("alpha", payload)
    controller.stores["alpha"].result(payload)
    require(identity(start["pid"]) == start["pid_start"], "provider active after actual callback")
    require(start["pgid"] != os.getpgid(controller.daemon.pid), "provider outside daemon PGID")
    controller.evidence()
    witness = dict(provider=start, callback=callback, daemon=dict(pid=controller.daemon.pid,
                   start=identity(controller.daemon.pid)), controller=dict(pid=os.getpid(), start=identity(os.getpid())),
                   ports=[controller.port, controller.relay.port], socket=str(controller.socket_path),
                   browser_started=True, at=time.monotonic())
    temporary = controller.root / "cancellation-ready.tmp"
    temporary.write_text(json.dumps(witness))
    temporary.replace(controller.root / "cancellation-ready.json")
    # No product cancellation operation or callback mutation. External supervisor
    # must signal the launcher; returning normally is a failed diagnostic.
    while controller.remaining(1):
        time.sleep(0.05)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--deadline", type=float, required=True)
    parser.add_argument("--exercise", choices=["baseline", "cancellation"], default="baseline")
    args = parser.parse_args()
    controller = Controller(args)
    def interrupted(signum: int, _frame: object) -> None:
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, signal.SIG_IGN)
        raise RuntimeError(f"controller interrupted by signal {signum}")
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, interrupted)
    try:
        begin = time.monotonic()
        controller.bootstrap()
        controller.phases.append(dict(name="setup", seconds=time.monotonic() - begin))
        from scenarios import exercise
        begin = time.monotonic()
        try:
            if args.exercise == "cancellation":
                cancellation_hold(controller)
            else:
                exercise(controller)
        finally:
            controller.phases.append(dict(name="actions", seconds=time.monotonic() - begin))
    except BaseException:
        (controller.root / "raw" / "failure.txt").write_text(traceback.format_exc())
        raise
    finally:
        try:
            begin = time.monotonic()
            try:
                controller.close()
            finally:
                controller.phases.append(dict(name="cleanup-drain", seconds=time.monotonic() - begin))
        finally:
            controller.evidence()


if __name__ == "__main__":
    main()
