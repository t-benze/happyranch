"""Public bootstrap, real providers/callback CLI and independent observations."""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import math
import os
import shlex
import shutil
import signal
import stat
import socket
import select
import threading
import subprocess
import sys
import time
import traceback
from pathlib import Path
from contextlib import contextmanager
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from oracles import Store, require
from transport import Relay


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _failure_stage(stage: object) -> str:
    if type(stage) is not str:
        return "other"
    for category, names in (
        ("phase", ("phase",)), ("bounded", ("bounded",)),
        ("restore", ("phase-state-restore", "bound-state-restore", "timer-restore")),
        ("context", ("redaction-context-unavailable", "context")),
        ("action", ("controller-action",)),
        ("teardown", ("controller-close", "thread-proxy-release", "late-transport-release",
                      "provider-observer-join", "provider-observer-incomplete", "browser-teardown",
                      "thread-proxy-close", "thread-proxy-closure", "late-transport-close", "late-transport-closure",
                      "peer-socket-shutdown", "peer-stream-close", "peer-socket-close", "provider-server-close",
                      "provider-socket-unlink", "cli-relay-close", "daemon-term", "daemon-wait", "daemon-log-close")),
        ("evidence", ("controller-evidence", "stage-main-browser-trace", "stage-thread-browser-trace",
                      "stage-scenario", "stage-browser-trace", "stage-primary-error", "export-e07",
                      "export-launch-witness", "export-observations", "export-scenario", "export-browser-trace")),
        ("browser", ("original-body-primary", "original-body-outcome", "native-page-close/crash",
                     "native-observation", "native-observation-secondary", "native-tracker-detach",
                     "bridge-wakeup-detach", "bridge-task-cancel", "group-local-detach", "terminal-stop-enforcement",
                     "terminal-stop-exit", "terminal-stop-cancel", "terminal-stop", "terminal-tracker-detach",
                     "browser-final-observation", "thread-context-close", "browser-context-close", "browser-close",
                     "browser-terminal-stop", "browser-tracker-detach")),
    ):
        if stage in names:
            return category
    return "other"


def _failure_error(error: BaseException) -> str:
    """Closed subclass precedence; never inspect text, names, args or chains."""
    for kinds, category in (
        (UnicodeError, "unicode"), (TimeoutError, "timeout"),
        ((KeyboardInterrupt, SystemExit, GeneratorExit, InterruptedError), "interruption"),
        (AssertionError, "assertion"), (ValueError, "value"),
        (OSError, "io"), (RuntimeError, "runtime"),
    ):
        if isinstance(error, kinds):
            return category
    return "other"


def _failure_slot(stage: str, error: BaseException) -> tuple[str, str, float] | None:
    at = time.monotonic()
    if type(at) not in (int, float) or not 0 <= at <= 9007199254740991 or not math.isfinite(at):
        return None
    return stage, _failure_error(error), at


def _failure_object(slot: tuple | None) -> dict | None:
    return dict(zip(("stage", "error", "at"), slot)) if slot is not None else None


def _failure_source(value: object) -> bool:
    return type(value) is str and len(value) == 40 and all(c in "0123456789abcdef" for c in value)


@contextmanager
def _failure_directories(root: Path, check: Callable[[], None], *, coord: bool = True) -> Any:
    """Pin literal no-follow ancestors; no repair or racing-rename guarantee."""
    spelling = str(root)
    parts = spelling.split("/")[1:]
    if not spelling.startswith("/") or not parts or any(p in ("", ".", "..") for p in parts):
        raise ValueError("invalid diagnostic root")
    descriptors = []
    links = []

    def verify() -> None:
        for parent, name, descriptor, original, private in links:
            check()
            named = os.stat(name, dir_fd=parent, follow_symlinks=False)
            opened = os.fstat(descriptor)
            if (not stat.S_ISDIR(named.st_mode) or not stat.S_ISDIR(opened.st_mode)
                    or (named.st_dev, named.st_ino) != (original.st_dev, original.st_ino)
                    or (opened.st_dev, opened.st_ino) != (original.st_dev, original.st_ino)
                    or (private and (named.st_uid != os.geteuid() or opened.st_uid != os.geteuid()
                                     or stat.S_IMODE(named.st_mode) != 0o700
                                     or stat.S_IMODE(opened.st_mode) != 0o700))):
                raise ValueError("diagnostic directory identity changed")
            check()

    try:
        check()
        current = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(current)
        check()
        for index, name in enumerate(parts + (["coord"] if coord else [])):
            check()
            named = os.stat(name, dir_fd=current, follow_symlinks=False)
            if not stat.S_ISDIR(named.st_mode):
                raise ValueError("aliased diagnostic directory")
            check()
            descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            descriptors.append(descriptor)
            info = os.fstat(descriptor)
            if (named.st_dev, named.st_ino) != (info.st_dev, info.st_ino):
                raise ValueError("diagnostic directory changed before open")
            links.append((current, name, descriptor, info, index >= len(parts) - 1))
            current = descriptor
            verify()
        yield current, verify
        verify()
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _failure_file(info: os.stat_result) -> None:
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
        raise ValueError("unsafe diagnostic file")


class Controller:
    def __init__(self, args: argparse.Namespace) -> None:
        self.root, self.source = args.root, args.source
        self.revision, self.deadline = args.sha, args.deadline
        self.local_bounds = threading.local()
        self.timer_phases: list[tuple[str, float]] = []
        delay, interval = signal.getitimer(signal.ITIMER_REAL)
        require(interval == 0, "controller requires a one-shot timer owner")
        self.inherited_alarm = time.monotonic() + delay if delay > 0 else float("inf")
        self.primary_error: BaseException | None = None
        self.primary_traceback: Any = None
        self.failures: list[dict] = []
        self._failure_raw: dict[str, tuple] = {}
        self._failure_slots: dict[str, tuple] = {}
        self._failure_writer_owner = threading.get_ident()
        self._failure_writer_active = False
        self._failure_publication_state = "not_attempted"
        self._failure_publication_failed = False
        self._failure_published: tuple | None = None
        self.observer_join: dict = dict(attempted=False, reason="nonterminal")
        self.redaction_ready = False
        self.boots = []
        self.env = dict(os.environ, E2E_SCENARIO_DEADLINE=str(args.deadline))
        self.publish_end()
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
        self.stores = {org: Store(self.root, org, self.effective_end) for org in ("alpha", "beta")}
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
        except BaseException as capture_error:
            # Keep the original [] attempt and its exception precedence. This
            # diagnostic slot says nothing about successful invalidation.
            try:
                (self.root / "coord/redaction-context.json").write_text("[]")
            except BaseException:
                invalidation_failed = True
                try:
                    self._latch_capture_failure(capture_error)
                except BaseException:
                    pass
                raise
            invalidation_failed = False
            try:
                self._latch_capture_failure(capture_error)
            except BaseException:
                pass
            raise

    def provider_cli(self, start: dict, action: str, **arguments: Any) -> dict:
        from owned import identity
        require(identity(start["pid"]) == start["pid_start"], "provider CLI requires live issued SID")
        prior = [r["action"] for r in self.provider_commands if r["provider"]["session"] == start["session"]]
        require("expected_exit" not in arguments, "closed provider exit policy")
        refusal = action == "jobs-refuse-review" or prior[:1] == ["jobs-refuse-review"]
        if refusal:
            require(not any(r.get("provider", {}).get("session") == start["session"] for r in self.thread_commands + self.e07_commands),
                    "refusal cannot mix provider modes")
            require((not prior and action == "jobs-refuse-review") or (prior == ["jobs-refuse-review"] and action == "jobs-final"),
                    "closed refusal then final protocol")
            if action == "jobs-final":
                require(arguments["payload"] == dict(status="completed", confidence=90,
                    summary="E04_REVIEW_REQUIRED_REFUSED no job submitted",
                    decision=dict(action="done", summary="E04_REVIEW_REQUIRED_REFUSED no job submitted")), "truthful refusal continuation")
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
        connection.settimeout(self.remaining(min(10, deadline-time.monotonic())))
        stream.flush()
        require(time.monotonic() < deadline, "provider CLI send exhausted exchange")
        connection.settimeout(self.remaining(deadline - time.monotonic()))
        receipt = json.loads(stream.readline(262144))
        receipt["wire"] = list(self.relay.records[offset:])
        self.provider_commands.append(receipt)  # Preserve failed commands before assertions.
        self.remaining(deadline-time.monotonic())
        attempt.update(wire_end=len(self.relay.records), finished=time.monotonic(), command_index=len(self.provider_commands) - 1)
        require(receipt["provider"] == {k: start[k] for k in ("task", "session", "agent", "org", "pid")}
                and receipt["source_sha"] == self.revision and receipt["python"] == self.python,
                "provider-owned CLI identity/source/interpreter", receipt)
        require(receipt["action"] == action and receipt["exit"] == (2 if action == "jobs-refuse-review" else 0) and receipt["timed_out"] is False
                and receipt["execution_deadline"] == instruction["execution_deadline"]
                and receipt["finished"] <= time.monotonic() < deadline and self.relay.error is None, "actual provider CLI exit/relay", receipt)
        if "payload" in arguments:
            expected = dict(arguments["payload"], task_id=start["task"], session_id=start["session"])
            if action not in {"jobs-submit", "jobs-refuse-review"}:
                expected["agent"] = start["agent"]
            require(receipt["payload"] == expected, "actual provider-authored payload", receipt["payload"])
        if action == "jobs-refuse-review":
            file = receipt["payload_file"]
            raw = json.dumps(receipt["payload"], sort_keys=True).encode("utf-8")
            require(start["org"] == "alpha" and receipt["payload"]["review_required"] is True
                    and file == dict(path=receipt["argv"][-2], text=raw.decode("utf-8"), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
                    and receipt["stdout"] == "" and receipt["stderr"] == (
                        f"Error reading scripts-submit file {file['path']}: review_required_removed: obtain any required authorization in a thread before submitting; "
                        "upgrade old clients and omit review_required or set it to false. This request was not submitted.\n")
                    and receipt["wire"] == [] and attempt["wire_begin"] == attempt["wire_end"]
                    and identity(start["pid"]) == start["pid_start"], "exact pre-HTTP refusal and live provider", receipt)
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

    def effective_end(self) -> float:
        return min(self.deadline, getattr(self, "case_deadline", self.deadline),
                   getattr(self.local_bounds, "end", self.deadline),
                   getattr(self, "observation_end", self.deadline), self.inherited_alarm)

    def _timer_sync(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            return
        end = min(self.effective_end(), self.inherited_alarm,
                  *(bound for _, bound in self.timer_phases))
        native = bool(self.timer_phases and self.timer_phases[-1][0] == "native")
        left = end - time.monotonic()
        # An expired owner is never restored as its old duration. The caller
        # checks expiry synchronously; no short alarm enters native dispatch.
        signal.setitimer(signal.ITIMER_REAL, left if left > 0 and not native else 0)

    @contextmanager
    def phase(self, end: float, *, native: bool = False) -> Any:
        require(threading.current_thread() is threading.main_thread(), "native/Python phase main-thread owner")
        bound = min(end, self.effective_end(), self.inherited_alarm,
                    *(value for _, value in self.timer_phases))
        require(bound > time.monotonic(), "phase absolute deadline exhausted")
        depth = len(self.timer_phases)
        error: BaseException | None = None
        error_traceback: Any = None
        try:
            self.timer_phases.append(("native" if native else "python", bound))
            self._timer_sync()
            yield
            require(time.monotonic() < bound, "phase absolute deadline exhausted")
        except BaseException as exc:
            # Caller recording happens after this unwind. Pin the actual
            # incoming object/traceback before an armed alarm can interrupt it.
            error, error_traceback = exc, exc.__traceback__
            self.record_error("phase", exc)
            raise
        finally:
            try:
                try:
                    del self.timer_phases[depth:]
                except BaseException as exc:
                    if error is None:
                        error, error_traceback = exc, exc.__traceback__
                    self.record_error("phase-state-restore", exc)
                finally:
                    try:
                        self._timer_sync()
                    except BaseException as exc:
                        if error is None:
                            error, error_traceback = exc, exc.__traceback__
                        self.record_error("timer-restore", exc)
            finally:
                if error is not None:
                    raise error.with_traceback(error_traceback)

    def publish_end(self) -> None:
        end = self.effective_end()
        require(end > time.monotonic(), "effective work deadline exhausted")
        path = self.root / "coord/work-end.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(dict(scenario_deadline=self.deadline, effective_end=end)))
        temporary.replace(path)
        if getattr(self, "browser_relay", None) and not getattr(self, "browser_relay_closed", False):
            self.browser_relay.deadline = end
        self._timer_sync()
        require(end > time.monotonic(), "deadline exhausted while publishing bound")

    @contextmanager
    def bounded(self, end: float) -> Any:
        previous = getattr(self.local_bounds, "end", self.deadline)
        error: BaseException | None = None
        error_traceback: Any = None
        try:
            self.local_bounds.end = min(previous, end)
            self.remaining()
            self._timer_sync()
            yield
            self.remaining()
        except BaseException as exc:
            error, error_traceback = exc, exc.__traceback__
            self.record_error("bounded", exc)
            raise
        finally:
            try:
                try:
                    self.local_bounds.end = previous
                except BaseException as exc:
                    if error is None:
                        error, error_traceback = exc, exc.__traceback__
                    self.record_error("bound-state-restore", exc)
                finally:
                    try:
                        self._timer_sync()
                    except BaseException as exc:
                        if error is None:
                            error, error_traceback = exc, exc.__traceback__
                        self.record_error("timer-restore", exc)
            finally:
                if error is not None:
                    raise error.with_traceback(error_traceback)

    def remaining(self, cap: float = 30) -> float:
        if not self.observer_stop.is_set():
            require(self.observer_error is None and self.observer_thread.is_alive(), "continuous provider observation", self.observer_error)
        remaining = self.effective_end() - time.monotonic()
        require(remaining > 0 and cap > 0, "action deadline exceeded")
        return min(remaining, cap)

    def poll(self, predicate: Callable[[], Any], name: str, cap: float = 30) -> Any:
        end = time.monotonic() + self.remaining(cap)
        while time.monotonic() < end:
            with self.bounded(end):
                value = predicate()
            if value:
                return value
            time.sleep(min(0.05, max(0, end - time.monotonic())))
        raise AssertionError(f"deadline waiting for {name}")

    def api(self, method: str, path: str, body: dict | None = None, bearer: str | None = "valid",
            *, review_refusal_file: dict | None = None) -> tuple[int, dict]:
        headers = {"Content-Type": "application/json"}
        if bearer is not None:
            headers["Authorization"] = "Bearer " + (self.token if bearer == "valid" else bearer)
        entity = json.dumps(body) if body is not None else None
        capture = None
        if review_refusal_file is not None:
            from owned import identity
            require(method == "POST" and path == "/api/v1/orgs/alpha/jobs/submit" and bearer == "valid"
                    and body["review_required"] is True, "selected refusal API capture only")
            entity = json.dumps(body, sort_keys=True).encode("utf-8")
            require(review_refusal_file == dict(path=review_refusal_file["path"], text=entity.decode("utf-8"),
                    bytes=len(entity), sha256=hashlib.sha256(entity).hexdigest()), "identical actual provider file entity")
            capture = dict(method=method, path=path, capture="E04.review-required-refused", index=len(self.http),
                entered=time.monotonic(), source_sha=self.revision, payload_file=review_refusal_file,
                destination=dict(host="127.0.0.1", port=self.port), daemon_pid=self.daemon.pid,
                daemon_start=identity(self.daemon.pid), boot=dict(self.boots[-1]))
            self.http.append(capture)  # Partial evidence precedes connect/send/assertions.
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=self.remaining())
        try:
            connection.connect()
            upstream_socket = connection.sock
            self.remaining()
            connection.sock.settimeout(self.remaining())
            if capture is not None:
                capture.update(peer=list(upstream_socket.getpeername()), send=time.monotonic(),
                    request_text=entity.decode("utf-8"), request_bytes=len(entity), request_sha256=hashlib.sha256(entity).hexdigest())
            connection.request(method, path, entity, headers)
            self.remaining()
            connection.sock.settimeout(self.remaining())
            response = connection.getresponse()
            self.remaining()
            upstream_socket.settimeout(self.remaining())
            if capture is not None:
                capture.update(response_at=time.monotonic(), status=response.status)
            raw = response.read()
            if capture is not None:
                capture.update(response_text=raw.decode("utf-8"), response_bytes=len(raw), response_sha256=hashlib.sha256(raw).hexdigest())
            data = json.loads(raw)
            self.remaining()
            if capture is None:
                self.http.append(dict(method=method, path=path, status=response.status, response=data))
            else:
                capture["response"] = data
            return response.status, data
        except BaseException as exc:
            if capture is not None:
                capture["error"] = repr(exc)
                partial = getattr(exc, "partial", None)
                if isinstance(partial, bytes):
                    capture["partial_response_hex"] = partial.hex()
            raise
        finally:
            if capture is not None:
                capture["finished"] = time.monotonic()
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
        self.remaining()
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

    def discover_daemon(self, record: dict) -> None:
        home = self.root / "daemon"
        token_path = home / "daemon.token"
        if token_path.exists():
            token = token_path.read_text().strip()
            if token:
                self.secrets.add(token)
                self.remember_thread_secrets([])
                record["bearer_sha256"] = hashlib.sha256(token.encode()).hexdigest()
        port_path = home / "daemon.port"
        if port_path.exists():
            port = int(port_path.read_text())
            path = self.root / "ports.json"
            ports = json.loads(path.read_text()) if path.exists() else []
            if port not in ports:
                path.write_text(json.dumps([*ports, port]))
            record.setdefault("discovered_ports", [])
            if port not in record["discovered_ports"]:
                record["discovered_ports"].append(port)

    def bootstrap(self) -> None:
        self.wrappers()
        program = f"import sys,runpy;sys.path.insert(0,{str(self.source)!r});runpy.run_module('runtime.daemon',run_name='__main__')"
        self.daemon_log = (self.root / "raw" / "daemon.txt").open("w")
        self.daemon = subprocess.Popen([self.python, "-I", "-c", program], cwd=self.root,
                                       env=self.env, stdout=self.daemon_log, stderr=subprocess.STDOUT, start_new_session=True)
        from e07 import register_owned
        from owned import identity
        register_owned(self, [self.daemon.pid])
        self.boots.append(dict(pid=self.daemon.pid, start=identity(self.daemon.pid), entered=time.monotonic()))
        def ready() -> bool:
            require(self.daemon.poll() is None, "daemon died during bootstrap")
            home = self.root / "daemon"
            self.discover_daemon(self.boots[-1])
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
        self.discover_daemon(self.boots[-1])
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
        self.relay = Relay(self.port, self.prove_committed, self.effective_end)
        ports = json.loads((self.root / "ports.json").read_text())
        (self.root / "ports.json").write_text(json.dumps(sorted(set([*ports,self.port,self.relay.port]))))
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
        self.remaining()
        require(exit_code == 0 and identity(old["pid"]) != old["start"], "graceful owned daemon exit", exit_code)
        program = f"import sys,runpy;sys.path.insert(0,{str(self.source)!r});runpy.run_module('runtime.daemon',run_name='__main__')"
        self.daemon = subprocess.Popen([self.python, "-I", "-c", program], cwd=self.root,
                                       env=self.env, stdout=self.daemon_log, stderr=subprocess.STDOUT, start_new_session=True)
        from e07 import register_owned
        from owned import identity
        register_owned(self, [self.daemon.pid])
        self.boots.append(dict(pid=self.daemon.pid, start=identity(self.daemon.pid), entered=time.monotonic()))
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
            self.discover_daemon(self.boots[-1])
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
        self.publish_end()
        self.server.settimeout(self.remaining())
        connection, _ = self.server.accept()
        connection.settimeout(self.remaining())
        stream = connection.makefile("rwb")
        self.peer_connections.append((connection, stream))
        row = json.loads(stream.readline(4096))
        self.remaining()
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
        # The caller owns the existing finite I/O cap: E05 sets 5s; other
        # callers retain start/command's socket cap. Never replace it with 30s.
        cap = connection.gettimeout()
        require(cap is not None and cap > 0, "provider release requires finite caller cap")
        end = self.effective_end()
        with self.bounded(min(end, time.monotonic() + cap)):
            connection.settimeout(self.remaining(cap))
            stream.write(b'{"action":"exit"}\n')
            self.remaining()
        with self.bounded(min(end, time.monotonic() + cap)):
            connection.settimeout(self.remaining(cap))
            stream.flush()
            self.remaining()
        with self.bounded(min(end, time.monotonic() + cap)):
            connection.settimeout(self.remaining(cap))
            acknowledgement = json.loads(stream.readline())
            self.remaining()
        require(acknowledgement == {"exiting": True}, "provider exit acknowledgement")
        with self.bounded(min(end, time.monotonic() + cap)):
            connection.settimeout(self.remaining(cap))
            stream.close()
            connection.close()
            self.remaining()

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

    def _latch_capture_failure(self, error: BaseException) -> None:
        try:
            slot = _failure_slot("context", error)
            if slot is not None:
                self._failure_slots.setdefault("context_capture", slot)
        except BaseException:
            pass  # Diagnostic interruption must not replace capture/invalidation.

    def record_error(self, stage: str, error: BaseException) -> None:
        # Select actual object/traceback before time, legacy diagnostics or I/O.
        # Literal-key setdefault prevents a nested callback replacing the winner.
        winner = self._failure_raw.setdefault("first", (error, error.__traceback__, _failure_stage(stage)))
        self.primary_error, self.primary_traceback = winner[:2]
        try:
            slot = _failure_slot(winner[2], winner[0])
            if slot is not None:
                self._failure_slots.setdefault("first_observed", slot)
        except BaseException:
            pass  # Raw winner is authoritative even if category completion fails.
        self.failures.append(dict(stage=stage, error=type(error).__name__, at=time.monotonic()))

    def _stage_failure_categories(self) -> None:
        if (threading.get_ident() != self._failure_writer_owner or self._failure_writer_active
                or self._failure_publication_failed):
            return
        self._failure_writer_active = True
        try:
            def check() -> None:
                if time.monotonic() >= self.effective_end():
                    raise TimeoutError("diagnostic work deadline")

            check()
            slots = (self._failure_slots.get("first_observed"), self._failure_slots.get("context_capture"))
            if self._failure_published == slots:
                self._failure_publication_state = "skipped"
                return
            if not _failure_source(self.revision):
                raise ValueError("diagnostic source unavailable")
            envelope = dict(schema=1, source_sha=self.revision,
                            first_observed=_failure_object(slots[0]), context_capture=_failure_object(slots[1]))
            check()
            data = json.dumps(envelope, allow_nan=False, separators=(",", ":")).encode("utf-8", errors="strict")
            check()
            if len(data) > 4096:
                raise ValueError("diagnostic snapshot overflow")
            with _failure_directories(self.root, check) as (directory, verify):
                final, pending = "failure-categories.json", ".failure-categories.pending"

                def final_info() -> os.stat_result | None:
                    check()
                    try:
                        info = os.stat(final, dir_fd=directory, follow_symlinks=False)
                    except FileNotFoundError:
                        check()
                        return None
                    _failure_file(info)
                    check()
                    return info

                previous = final_info()
                check()
                try:
                    os.stat(pending, dir_fd=directory, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise ValueError("unfinished diagnostic publication")
                verify()
                check()
                descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
                                     dir_fd=directory)
                try:
                    check()
                    opened = os.fstat(descriptor)
                    _failure_file(opened)
                    named = os.stat(pending, dir_fd=directory, follow_symlinks=False)
                    _failure_file(named)
                    if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
                        raise ValueError("diagnostic pending identity changed")
                    verify()
                    check()
                    if os.write(descriptor, data) != len(data):
                        raise OSError("short diagnostic write")
                    check()
                finally:
                    os.close(descriptor)
                check()
                verify()
                named = os.stat(pending, dir_fd=directory, follow_symlinks=False)
                _failure_file(named)
                if ((opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
                        or named.st_size != len(data)):
                    raise ValueError("diagnostic pending identity changed")
                current = final_info()
                if ((None if current is None else (current.st_dev, current.st_ino))
                        != (None if previous is None else (previous.st_dev, previous.st_ino))):
                    raise ValueError("diagnostic final identity changed")
                verify()
                check()
                os.replace(pending, final, src_dir_fd=directory, dst_dir_fd=directory)
                check()
                committed = final_info()
                if committed is None or (committed.st_dev, committed.st_ino) != (opened.st_dev, opened.st_ino):
                    raise ValueError("diagnostic replacement identity changed")
                verify()
            check()
            self._failure_published = slots
            self._failure_publication_state = "published"
        except BaseException:
            self._failure_publication_state = "failed"
            self._failure_publication_failed = True
        finally:
            self._failure_writer_active = False

    def _attempt(self, stage: str, action: Callable[[], Any], *, blocking: bool = True) -> Any:
        try:
            if blocking:
                end = self.effective_end()
                require(end > time.monotonic(), "cleanup unavailable: work exhausted")
                with self.bounded(end):
                    return action()
            return action()
        except BaseException as exc:
            self.record_error(stage, exc)
            return None

    def prepare_redaction(self) -> None:
        self.redaction_ready = False
        try:
            require(isinstance(self.token, str) and bool(self.token), "complete bearer redaction context")
            if self.threads_active:
                require(self.effective_end() > time.monotonic(), "full thread redaction context unavailable after expiry")
                self.capture_thread_secrets()
            self.redaction_ready = True
        except BaseException as exc:
            self.record_error("redaction-context-unavailable", exc)

    def _atomic_text(self, path: Path, text: str) -> None:
        require(self.redaction_ready, "unsafe evidence withheld without full redaction context")
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(self.sanitize(text))
        temporary.replace(path)

    def _atomic_json(self, path: Path, value: Any) -> None:
        self._atomic_text(path, json.dumps(value, indent=2))

    def stage_browser_trace(self) -> None:
        if self.browser:
            self._attempt("stage-main-browser-trace", lambda: self._atomic_json(
                self.root / "raw/browser-events.json", list(self.browser.events)), blocking=False)
        if self.browser and hasattr(self.browser, "thread_events"):
            self._attempt("stage-thread-browser-trace", lambda: self._atomic_json(
                self.root / "raw/e05-browser-events.json", list(self.browser.thread_events)), blocking=False)

    def stage_small_evidence(self) -> None:
        if not self.redaction_ready:
            return
        self._attempt("stage-scenario", lambda: self._atomic_json(self.root / "scenario.json",
                      dict(variants=self.results, scenario_phases=self.phases, failures=self.failures,
                           evidence_state="partial/unvalidated")), blocking=False)
        self._attempt("stage-browser-trace", self.stage_browser_trace, blocking=False)
        if self.primary_error is not None:
            self._attempt("stage-primary-error", lambda: self._atomic_text(self.root / "raw/failure.txt",
                "".join(traceback.format_exception(type(self.primary_error), self.primary_error, self.primary_traceback))),
                blocking=False)

    def observer_snapshot(self) -> tuple[list[dict], dict]:
        begin = time.monotonic()
        alive_before = self.observer_thread.is_alive()
        rows = list(self.observer)  # Full stable prefix; rows are never mutated.
        finished = time.monotonic()
        alive_after = self.observer_thread.is_alive()
        state = dict(stop_requested=self.observer_stop.is_set(), join=dict(self.observer_join),
                     alive_before=alive_before, alive_after=alive_after, error=self.observer_error,
                     copy_begin=begin, cutoff=finished, rows=len(rows),
                     partial=(alive_before or alive_after or self.observer_error is not None
                              or not self.observer_join.get("finished", False)))
        return rows, state

    def close(self) -> None:
        # Stop requested != observer terminated. Nothing here borrows Owned's
        # cleanup/export allowance or grants another controller work interval.
        self.observer_stop.set()
        for name, transport in (("thread-proxy", self.browser_relay), ("late-transport", self.late_transport)):
            if transport:
                self._attempt(name + "-release", transport.release.set, blocking=False)
        self.prepare_redaction()
        self.stage_small_evidence()  # Before any screenshot/exit/bulky snapshot.
        self.observer_join = dict(attempted=False, reason="work exhausted", entered=time.monotonic())
        available = self.effective_end() - time.monotonic()
        if available > 0:
            self.observer_join.update(attempted=True, reason=None)
            self._attempt("provider-observer-join", lambda: self.observer_thread.join(timeout=min(1, max(0, self.effective_end()-time.monotonic()))))
            self.observer_join["finished"] = not self.observer_thread.is_alive()
        self.observer_join.update(alive=self.observer_thread.is_alive(), cutoff=time.monotonic())
        if self.observer_error is not None or self.observer_thread.is_alive():
            self.record_error("provider-observer-incomplete", RuntimeError("observer error/live at cutoff"))
        if self.browser:
            self._attempt("browser-teardown", self.browser.close, blocking=False)
        if self.browser_relay:
            closure = self._attempt("thread-proxy-close", lambda: self.browser_relay.close(self.effective_end()))
            if closure is not None:
                self.browser_relay_closed = True
                self.durable.append(dict(thread_proxy_closure=closure))
                if closure["workers_alive"] or closure["listener_alive"] or closure["connections"] or closure["errors"]:
                    self.record_error("thread-proxy-closure", RuntimeError("proxy closure/transport failure"))
        if self.late_transport:
            closure = self._attempt("late-transport-close", lambda: self.late_transport.close(self.effective_end()))
            if closure is not None:
                self.e07["transport_closure"] = closure
                if closure["listener_alive"] or closure["workers_alive"] or closure["sockets"] or closure["errors"]:
                    self.record_error("late-transport-closure", RuntimeError("late transport closure failure"))
        for connection, stream in self.peer_connections:
            if self.effective_end() <= time.monotonic():
                self._attempt("peer-socket-shutdown", lambda connection=connection: connection.shutdown(socket.SHUT_RDWR), blocking=False)
            def close_stream(connection: Any = connection, stream: Any = stream) -> None:
                cap = connection.gettimeout()
                require(cap is not None and cap > 0, "peer retains finite socket cap")
                left = self.effective_end()-time.monotonic()
                require(left > 0, "peer stream close has existing positive time")
                connection.settimeout(min(cap, left))
                stream.close()
            self._attempt("peer-stream-close", close_stream)
            self._attempt("peer-socket-close", connection.close, blocking=False)
        self._attempt("provider-server-close", self.server.close, blocking=False)
        self._attempt("provider-socket-unlink", lambda: self.socket_path.unlink(missing_ok=True), blocking=False)
        if self.relay:
            self._attempt("cli-relay-close", lambda: self.relay.close(self.effective_end()))
        if self.daemon and self.daemon.poll() is None:
            self._attempt("daemon-term", lambda: self.daemon.send_signal(signal.SIGTERM), blocking=False)
            self._attempt("daemon-wait", lambda: self.daemon.wait(timeout=max(0, min(20, self.effective_end()-time.monotonic()))))
        if hasattr(self, "daemon_log"):
            self._attempt("daemon-log-close", self.daemon_log.close)

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
        # cancellation_hold calls this nonterminally: do not stop/join observer.
        self.prepare_redaction()
        self.stage_small_evidence()
        if not self.redaction_ready:
            raise RuntimeError("unsafe evidence withheld: full redaction context unavailable")
        observer, state = self.observer_snapshot()
        rows = dict(variants=self.results, starts=self.starts, commands=self.commands,
                    http=self.http, durable=self.durable, relay=list(self.relay.records) if self.relay else [],
                    provider_commands=self.provider_commands, provider_attempts=self.provider_attempts,
                    provider_observer=observer, observer_error=self.observer_error, observer_state=state,
                    failures=self.failures)
        if self.e07:
            def e07_evidence() -> None:
                launches, failures = self.collect_e07_launches()
                self.e07.update(partial_launches=launches, launch_record_failures=failures)
                self._atomic_json(self.root / "raw/e07.json", self.e07)
            self._attempt("export-e07", e07_evidence, blocking=False)
        rows.update(e07=self.e07, e07_commands=self.e07_commands, boots=self.boots)
        rows.update(thread_commands=self.thread_commands, thread_attempts=self.thread_attempts,
                    thread_proxy=list(self.browser_relay.records) if self.browser_relay else [])
        witness = self.root / "witness/identities.jsonl"
        if witness.exists():
            def witness_evidence() -> None:
                # Digest and parse exactly the same frozen genuine prefix.
                data = witness.read_bytes()
                rows["witness_bytes"] = len(data)
                rows["witness_sha256"] = hashlib.sha256(data).hexdigest()
                rows["witness_incomplete_line"] = bool(data and not data.endswith(b"\n"))
                self._atomic_text(self.root / "raw/launch-witnesses.jsonl", data.decode())
                rows["launch_witnesses"] = [json.loads(line) for line in data.split(b"\n")[:-1]]
            self._attempt("export-launch-witness", witness_evidence, blocking=False)
        self._attempt("export-observations", lambda: self._atomic_json(self.root / "raw/observations.json", rows), blocking=False)
        self._attempt("export-scenario", lambda: self._atomic_json(self.root / "scenario.json",
            dict(variants=self.results, scenario_phases=self.phases, e07=self.e07, failures=self.failures,
                 observer_state=state)), blocking=False)
        self._attempt("export-browser-trace", self.stage_browser_trace, blocking=False)
        # All available evidence attempts precede the existing final assertion.
        require(not self.e07.get("launch_record_failures"), "E07 launch record evidence incomplete")
        require(not self.failures, "evidence/cleanup failed", self.failures)


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
    def expired(_signum, _frame):
        raise TimeoutError("controller absolute work deadline")
    signal.signal(signal.SIGALRM, expired)
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
    except BaseException as exc:
        # Save the actual first exception/traceback before any cleanup/export.
        controller.record_error("controller-action", exc)
        try:
            controller._stage_failure_categories()
        finally:
            if controller.browser:
                controller.browser.poison(exc)  # No dispatcher re-entry after interruption.
    finally:
        begin = time.monotonic()
        try:
            controller.close()
        except BaseException as exc:
            controller.record_error("controller-close", exc)
        finally:
            controller.phases.append(dict(name="cleanup-drain", seconds=time.monotonic() - begin))
        controller._stage_failure_categories()
        try:
            controller.evidence()
        except BaseException as exc:
            controller.record_error("controller-evidence", exc)
        controller._stage_failure_categories()
        if controller.primary_error is not None:
            raise controller.primary_error.with_traceback(controller.primary_traceback)


if __name__ == "__main__":
    main()
