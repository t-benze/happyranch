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
import select
import threading
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
        self.secrets: set[str] = set()
        self.secret_lock = threading.RLock()
        self.threads_active = False
        self.provider_commands: list[dict] = []
        self.provider_attempts: list[dict] = []
        self.observer: list[dict] = []
        self.observer_error: str | None = None
        self.observer_stop = threading.Event()
        self.daemon = None
        self.relay = None
        self.browser = None
        self.browser_relay = None
        self.e07: dict = {}
        self.e07_commands: list[dict] = []
        self.late_transport = None
        self.thread_commands: list[dict] = []
        self.thread_attempts: list[dict] = []
        self.starts: list[dict] = []
        self.holds: dict[str, tuple] = {}
        self.peer_connections: list[tuple] = []
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
        self.observer_thread = threading.Thread(target=self.observe_providers, daemon=True)
        self.observer_thread.start()

    def observe_providers(self) -> None:
        """Passive continuous socket/witness observer, including inter-case gaps."""
        try:
            while not self.observer_stop.is_set():
                path = self.root / "witness/identities.jsonl"
                # A writer may be appending its single bounded record; retain
                # complete lines only, with the next observation covering it.
                data = path.read_bytes() if path.exists() else b""
                lines = data.split(b"\n")[:-1]
                rows = [json.loads(line) for line in lines]
                self.observer.append(dict(at=time.monotonic(), pending=bool(select.select([self.server], [], [], 0)[0]),
                                          stubs=sum(r["kind"] == "stub" for r in rows),
                                          calls=sum(r["kind"] == "callback" for r in rows)))
                if self.observer_stop.wait(0.05):
                    break
        except BaseException as exc:
            self.observer_error = repr(exc)

    def sanitize(self, text: str) -> str:
        for secret in sorted(self.secrets | {self.token}, key=len, reverse=True):
            if secret:
                text = text.replace(secret, "[REDACTED]")
        return text

    def remember_thread_secrets(self, tokens: list[str]) -> None:
        with self.secret_lock:
            require(all(isinstance(token, str) and token for token in tokens), "readable thread redaction identities")
            self.secrets.update(tokens)
            temporary = self.root / "coord/thread-redaction.tmp"
            temporary.write_text(json.dumps(sorted(self.secrets)))
            temporary.chmod(0o600)
            temporary.replace(self.root / "coord/redaction-context.json")

    def capture_thread_secrets(self) -> None:
        if not self.threads_active:
            return
        try:
            tokens = [row["invocation_token"] for store in self.stores.values()
                      for row in store.query("SELECT invocation_token FROM thread_invocations")]
            self.remember_thread_secrets(tokens)
        except BaseException:
            # Existing launcher rejects an invalid private context and withholds
            # raw exports. Never fall back to bearer-only on an unknown token set.
            (self.root / "coord/redaction-context.json").write_text("[]")
            raise

    def provider_cli(self, start: dict, action: str, **arguments: Any) -> dict:
        from owned import identity
        require(identity(start["pid"]) == start["pid_start"], "provider CLI requires live issued SID")
        connection, stream = self.holds[start["session"]]
        entered = time.monotonic()
        deadline = min(entered + self.remaining(10), self.deadline, getattr(self, "case_deadline", self.deadline))
        # One absolute exchange end; the child stops one second earlier so its
        # partial receipt can reach the existing sanitized finally exporter.
        require(deadline - entered > 1, "provider CLI requires receipt delivery room")
        instruction = dict(action=action, deadline=deadline, execution_deadline=deadline - 1, **arguments)
        offset = len(self.relay.records)
        attempt = dict(provider=dict(start), instruction=instruction, entered=entered, wire_begin=offset)
        self.provider_attempts.append(attempt)
        send_remaining = deadline - time.monotonic()
        require(send_remaining > 0, "provider CLI exchange exhausted before send")
        connection.settimeout(send_remaining)
        stream.write((json.dumps(instruction) + "\n").encode())
        stream.flush()
        require(time.monotonic() < deadline, "provider CLI send exhausted exchange")
        connection.settimeout(deadline - time.monotonic())
        receipt = json.loads(stream.readline(262144))
        receipt["wire"] = list(self.relay.records[offset:])
        self.provider_commands.append(receipt)  # Preserve failed commands before assertions.
        attempt.update(finished=time.monotonic(), command_index=len(self.provider_commands) - 1)
        require(receipt["provider"] == {k: start[k] for k in ("task", "session", "agent", "org", "pid")}
                and receipt["source_sha"] == self.revision and receipt["python"] == self.python,
                "provider-owned CLI identity/source/interpreter", receipt)
        require(receipt["action"] == action and receipt["exit"] == 0 and receipt["timed_out"] is False
                and receipt["execution_deadline"] == instruction["execution_deadline"]
                and receipt["finished"] <= time.monotonic() < deadline and self.relay.error is None, "actual provider CLI exit/relay", receipt)
        if "payload" in arguments:
            expected = dict(arguments["payload"], task_id=start["task"], session_id=start["session"])
            if action != "jobs-submit":
                expected["agent"] = start["agent"]
            require(receipt["payload"] == expected, "actual provider-authored payload", receipt["payload"])
        if action == "jobs-submit":
            submitted = [r for r in receipt["wire"] if r["path"] == f"/api/v1/orgs/{start['org']}/jobs/submit" and r["method"] == "POST"]
            require(len(submitted) == 1 and submitted[0]["status"] == 201
                    and submitted[0]["response"] == json.loads(receipt["stdout"]) and not submitted[0]["dropped"],
                    "actual submit HTTP201 and returned identity", submitted)
        if action in {"jobs-block", "jobs-final"}:
            payload = receipt["payload"]
            expected = f"/api/v1/orgs/{start['org']}/tasks/{start['task']}/completion"
            callbacks = [r for r in receipt["wire"] if r["path"] == expected and r["method"] == "POST"]
            require(len(callbacks) == 1 and callbacks[0]["status"] == 200
                    and callbacks[0]["response"] == {"ok": True} and not callbacks[0]["dropped"], "real final callback", callbacks)
            self.payloads[start["session"]] = payload
        return receipt

    def remaining(self, cap: float = 30) -> float:
        require(self.observer_error is None and self.observer_thread.is_alive(), "continuous provider observation", self.observer_error)
        remaining = min(self.deadline, getattr(self, "case_deadline", self.deadline)) - time.monotonic()
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
            "import sys, pathlib, hashlib, runpy, os, json; "
            f"s=pathlib.Path({str(self.source)!r}); "
            f"assert hashlib.sha256((s/'cli/main.py').read_bytes()).hexdigest()=={cli_hash!r}; "
            "sys.path.insert(0,str(s)); import cli.main; "
            "assert pathlib.Path(cli.main.__file__).resolve()==s/'cli/main.py'; "
            f"g=runpy.run_path({str(guard)!r},run_name='e2e_external_witness'); "
            f"g['witness']({{'kind':'callback','source_sha':{self.revision!r},'cli_sha256':{cli_hash!r},'pid':os.getpid(),'ppid':os.getppid(),'argv_sha256':hashlib.sha256(json.dumps(sys.argv[1:]).encode()).hexdigest()}}); "
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
        thread_plan = self.root / "coord/thread-plan.sh"
        thread_plan.write_text(f"#!/bin/sh\nexec {shlex.quote(self.python)} -I {shlex.quote(str(self.source / 'e2e/thread_provider.py'))} \"$@\"\n")
        for file in [*binary.iterdir(), plan, thread_plan]:
            file.chmod(0o700)
        Path(str(plan) + ".sha256").write_text(sha(plan))
        Path(str(thread_plan) + ".sha256").write_text(sha(thread_plan))
        manifest = dict(root=str(self.root), source=str(self.source), revision=self.revision,
                        python=self.python, callback=str(callback), callback_sha256=sha(callback), stubs=stubs)
        path = self.root / "coord" / "manifest.json"
        path.write_text(json.dumps(manifest))
        path.chmod(0o600)
        self.env.update(PATH=f"{binary}:/usr/bin:/bin", E2E_SOCKET=str(self.socket_path),
                        FAKE_CLAUDE_PLAN=str(plan), FAKE_CLAUDE_THREAD_PLAN=str(thread_plan), HAPPYRANCH_TEST_PARENT_MANIFEST=str(path),
                        HAPPYRANCH_TEST_STUB_GUARD=str(guard), HAPPYRANCH_TEST_WITNESS_DIR=str(self.root / "witness"))
        self.bindings = {str(p): sha(p) for p in [*binary.iterdir(), plan, Path(str(plan) + ".sha256"),
                                               thread_plan, Path(str(thread_plan) + ".sha256"), path]}
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
            self.secrets.add(self.token)
            secrets_path = self.root / "coord/redaction-context.json"
            secrets_path.write_text(json.dumps(sorted(self.secrets)))
            secrets_path.chmod(0o600)
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

    def restart_owned_daemon(self) -> dict:
        """Restart only our quiescent disposable process, keeping public files."""
        from owned import identity
        require(not self.holds, "restart has no held providers")
        require(self.daemon is not None and self.daemon.poll() is None, "owned daemon live")
        old = dict(pid=self.daemon.pid, start=identity(self.daemon.pid), port=self.port)
        require(old["start"] is not None, "restart old daemon identity")
        env = Path(f"/proc/{old['pid']}/environ").read_bytes().split(b"\0")
        require(f"E2E_ROOT={self.root}".encode() in env, "restart exact disposable ownership")
        ports = json.loads((self.root / "ports.json").read_text())
        begin = time.monotonic()
        # Pidfd binds the graceful stop to this exact process, even on PID reuse.
        descriptor = os.pidfd_open(old["pid"])
        try:
            require(identity(old["pid"]) == old["start"], "restart identity before signal")
            signal.pidfd_send_signal(descriptor, signal.SIGTERM)
        finally:
            os.close(descriptor)
        exit_code = self.daemon.wait(timeout=self.remaining(20))
        require(exit_code == 0 and identity(old["pid"]) != old["start"], "graceful owned daemon exit", exit_code)
        program = f"import sys,runpy;sys.path.insert(0,{str(self.source)!r});runpy.run_module('runtime.daemon',run_name='__main__')"
        self.daemon = subprocess.Popen([self.python, "-I", "-c", program], cwd=self.root,
                                       env=self.env, stdout=self.daemon_log, stderr=subprocess.STDOUT, start_new_session=True)
        new = dict(pid=self.daemon.pid, start=identity(self.daemon.pid))
        require(new["start"] is not None and (new["pid"], new["start"]) != (old["pid"], old["start"]),
                "new owned daemon identity", new)
        # Retain both identities before readiness so failure artifacts and the
        # unchanged outer descendant watcher can reconcile a partial restart.
        record = dict(old=old, new=new, exit=exit_code)
        self.durable.append(dict(pause_daemon_restart=record))
        def ready() -> bool:
            require(self.daemon.poll() is None and identity(new["pid"]) == new["start"], "restarted daemon live")
            home = self.root / "daemon"
            if not all((home / f"daemon.{name}").exists() for name in ("port", "token", "pid")):
                return False
            if int((home / "daemon.pid").read_text()) != new["pid"]:
                return False
            self.port = int((home / "daemon.port").read_text())
            self.token = (home / "daemon.token").read_text().strip()
            self.secrets.add(self.token)
            secrets_path = self.root / "coord/redaction-context.json"
            secrets_path.write_text(json.dumps(sorted(self.secrets)))
            secrets_path.chmod(0o600)
            # Register every discovered port, including on failed readiness.
            if self.port not in ports:
                ports.append(self.port)
                (self.root / "ports.json").write_text(json.dumps(ports))
            try:
                return self.get("/api/v1/health")["status"] == "ok"
            except ConnectionError:
                return False
        self.poll(ready, "same-files owned daemon restart readiness")
        self.relay.upstream = self.port
        token = self.root / "client" / "daemon.token"
        token.write_text(self.token)
        token.chmod(0o600)
        self.browser.base = f"http://127.0.0.1:{self.port}"
        self.validate_bindings()
        record["new"]["port"] = self.port
        record["seconds"] = time.monotonic() - begin
        self.phases.append(dict(name="pause-owned-daemon-restart", **record))
        return record

    def start(self, org: str, task: str | None, agent: str) -> dict:
        self.validate_bindings()
        self.server.settimeout(self.remaining())
        connection, _ = self.server.accept()
        connection.settimeout(self.remaining())
        stream = connection.makefile("rwb")
        self.peer_connections.append((connection, stream))
        row = json.loads(stream.readline(4096))
        import struct
        peer_pid, peer_uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        require(peer_pid == row["pid"] and peer_uid == os.getuid(), "actual owned socket peer", row)
        from owned import identity
        row["pid_start"] = identity(peer_pid)
        row["pgid"] = os.getpgid(peer_pid)
        require(row["pid_start"] is not None, "live provider identity")
        env = Path(f"/proc/{peer_pid}/environ").read_bytes().split(b"\0")
        require(f"E2E_ROOT={self.root}".encode() in env, "provider run ownership")
        # Preserve an unexpected peer in evidence/ownership before refusing it.
        self.durable.append(dict(provider_peer=dict(row)))
        temp = self.root / "owned.tmp"
        prior_owned = json.loads((self.root / "owned.json").read_text()) if (self.root / "owned.json").exists() else []
        temp.write_text(json.dumps(sorted(set(prior_owned + [s["pid"] for s in self.starts] + [peer_pid]))))
        temp.replace(self.root / "owned.json")
        self.holds[row["session"]] = (connection, stream)
        require(row["org"] == org and row["agent"] == agent and (task is None or row["task"] == task),
                "exact expected actual launch", row)
        require(row["session"] not in {s["session"] for s in self.starts}, "unique issued session", row)
        self.starts.append(row)
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
        try:
            self.capture_thread_secrets()
        except Exception:
            failures.append("unknown E05 token redaction context")
        if self.browser_relay:
            self.browser_relay.release.set()
            try:
                if self.browser:
                    self.browser.close_threads_context()
            except Exception as exc:
                failures.append(type(exc).__name__ + ": E05 browser close")
            finally:
                closure = self.browser_relay.close()
                self.durable.append(dict(thread_proxy_closure=closure))
                if closure["workers_alive"] or closure["listener_alive"] or closure["connections"] or closure["errors"]:
                    failures.append("E05 proxy closure/transport failure")
        if self.late_transport:
            closure = self.late_transport.close()
            self.e07["transport_closure"] = closure
            if closure["listener_alive"] or closure["workers_alive"] or closure["sockets"] or closure["errors"]:
                failures.append("E07 transport closure")
        if self.browser:
            try:
                self.browser.close()
            except Exception as exc:
                failures.append(repr(exc))
        for connection, stream in self.peer_connections:
            stream.close()
            connection.close()
        self.observer_stop.set()
        self.observer_thread.join(timeout=1)
        failures.extend([self.observer_error] if self.observer_error else [])
        if self.observer_thread.is_alive():
            failures.append("provider observer did not stop")
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

    def collect_e07_launches(self) -> tuple[list[dict], list[dict]]:
        """Retain each valid record; a missing sibling cannot erase evidence."""
        import math

        def number(value: object) -> bool:
            return type(value) in (int, float) and value > 0 and (type(value) is int or math.isfinite(value))

        records, failures = [], []
        expected = set()
        for index, command in enumerate(self.e07_commands):
            if command["action"] not in {"e07-parent", "e07-held"}:
                continue
            # The ledger is registered before sending the real instruction.
            # Even an interrupted send requires evidence or an explicit failure.
            start = command["provider"]
            binding = {k:start[k] for k in ("task", "session", "agent", "org", "pid")}
            path = self.root / "coord" / ("e07-launch-" + start["session"] + ".json")
            expected.add(path)
            reason = None
            try:
                record = json.loads(path.read_text())
            except TimeoutError:
                raise  # Do not turn the original deadline into a parse failure.
            except FileNotFoundError:
                reason = "missing"
            except (OSError, UnicodeError, ValueError, RecursionError):
                reason = "malformed"
            if reason is None:
                valid = (isinstance(record, dict) and record.get("action") == command["action"]
                         and record.get("provider") == binding and number(record.get("entered")))
                if valid and record.get("incomplete") is True:
                    valid = (set(record) == {"action","provider","child_pid","entered","incomplete"}
                             and type(record["child_pid"]) is int and record["child_pid"] > 1)
                    reason = "incomplete" if valid else "malformed"
                elif valid:
                    child = record.get("child")
                    valid = (set(record) == {"action","provider","child","argv","payload","payload_sha256",
                             "payload_bytes","callback_sha256","source_sha","python","entered",
                             "execution_deadline","acknowledged"}
                             and isinstance(child, dict)
                             and set(child) == {"pid","start","state","ppid","argv","cgroup","at"}
                             and type(child["pid"]) is int and child["pid"] > 1
                             and type(child["ppid"]) is int and child["ppid"] == start["pid"]
                             and all(isinstance(child[k], str) and child[k] for k in ("start","state","cgroup"))
                             and isinstance(child["argv"], list) and all(isinstance(a, str) for a in child["argv"])
                             and number(child["at"]) and isinstance(record["argv"], list)
                             and all(isinstance(a, str) for a in record["argv"])
                             and isinstance(record["payload"], dict)
                             and all(isinstance(record[k], str) and len(record[k]) == 64
                                     and all(c in "0123456789abcdef" for c in record[k])
                                     for k in ("payload_sha256","callback_sha256"))
                             and type(record["payload_bytes"]) is int and record["payload_bytes"] > 0
                             and record["source_sha"] == self.revision and record["python"] == self.python
                             and number(record["acknowledged"]) and number(record["execution_deadline"])
                             and record["entered"] <= record["acknowledged"] <= record["execution_deadline"]
                             and record["execution_deadline"] == command["instruction"]["execution_deadline"]
                             and ("launch" not in command or command["launch"] == record))
                if valid:
                    records.append(record)
                else:
                    reason = "malformed"
            if reason:
                # Fixed categories and ledger index only: never raw bytes,
                # filenames or exception text from a damaged private record.
                failures.append(dict(command=index, reason=reason))
        if any(path not in expected for path in (self.root / "coord").glob("e07-launch-*.json")):
            failures.append(dict(reason="unexpected"))
        return records, failures

    def evidence(self) -> None:
        self.capture_thread_secrets()
        rows = dict(variants=self.results, starts=self.starts, commands=self.commands,
                    http=self.http, durable=self.durable, relay=self.relay.records if self.relay else [],
                    provider_commands=self.provider_commands, provider_attempts=self.provider_attempts,
                    provider_observer=self.observer, observer_error=self.observer_error)
        if self.e07:
            launches, failures = self.collect_e07_launches()
            self.e07.update(partial_launches=launches, launch_record_failures=failures)
            (self.root / "raw/e07.json").write_text(self.sanitize(json.dumps(self.e07, indent=2)))
        rows.update(e07=self.e07, e07_commands=self.e07_commands)
        rows.update(thread_commands=self.thread_commands, thread_attempts=self.thread_attempts,
                    thread_proxy=self.browser_relay.records if self.browser_relay else [])
        witness = self.root / "witness/identities.jsonl"
        if witness.exists():
            # Keep genuine partial-run source witnesses even when no final case
            # or final closure record could be completed. Do not export headers.
            data = witness.read_bytes()
            rows["launch_witnesses"] = [json.loads(line) for line in data.split(b"\n")[:-1]]
            rows["witness_bytes"] = len(data)
            rows["witness_sha256"] = sha(witness)
            (self.root / "raw/launch-witnesses.jsonl").write_text(self.sanitize(data.decode()))
            rows["witness_incomplete_line"] = bool(data and not data.endswith(b"\n"))
        raw = self.sanitize(json.dumps(rows, indent=2))
        (self.root / "raw" / "observations.json").write_text(raw)
        (self.root / "scenario.json").write_text(self.sanitize(json.dumps(dict(variants=self.results, scenario_phases=self.phases, e07=self.e07))))
        # Fail only after all available sanitized evidence has been serialized.
        # Sanitation/context errors above remain fatal and are never caught here.
        require(not self.e07.get("launch_record_failures"), "E07 launch record evidence incomplete")


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
                from jobs import anchor, exercise as exercise_jobs
                anchor(controller, "initial-A", 0)
                exercise(controller)
                anchor(controller, "baseline-A", 9)
                from pause import exercise as exercise_pause
                exercise_pause(controller)
                exercise_jobs(controller)
                from threads import exercise as exercise_threads
                exercise_threads(controller)
                from e07 import exercise as exercise_cancellation
                exercise_cancellation(controller)
        finally:
            controller.phases.append(dict(name="actions", seconds=time.monotonic() - begin))
    except BaseException:
        controller.capture_thread_secrets()
        (controller.root / "raw" / "failure.txt").write_text(controller.sanitize(traceback.format_exc()))
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
