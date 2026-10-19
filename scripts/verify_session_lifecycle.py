#!/usr/bin/env python3
"""THR307 finite, disposable Linux lifecycle verifier (outside automatic CI).

Authority/conditions and four authoring answers: accepted TASK11962 case record,
sole ROOT11948 implementation-brief. No live org, callback or native writer.
Run only with the bounded task/job allocation documented in docs/local-ci.md.
F is this process and the subreaper; W is a controlled child, never a replacement
reaper. Rescue is recorded separately and cannot make backend assertions pass.
"""

from __future__ import annotations

import argparse
import builtins
import ctypes
import dataclasses
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from types import SimpleNamespace

RAW_OPEN = builtins.open
RAW_KILL = os.kill
RAW_KILLPG = os.killpg
RAW_SCANDIR = os.scandir
RAW_GETPGID = os.getpgid
TLS = threading.local()
CASES = (
    "C1-group-builtin", "C1-group-custom", "C1-escaped-known", "C1-late-in-group",
    "C2-cancel", "C2-timeout-pipe", "C2-shutdown-bind", "C3-root-gone",
    "C3-reused-identity", "C4-owner-isolation", "C5-unreadable", "C5-teardown-error",
)
BASELINE_CASES = ("C1-escaped-known", "C3-reused-identity")


def require(value, message):
    if not value:
        raise AssertionError(message)


def stat(pid):
    """Independent oracle; never calls the shipping census or patched open."""
    try:
        with RAW_OPEN(f"/proc/{pid}/stat", encoding="utf-8") as stream:
            raw = stream.read(16384)
        tail = raw[raw.rindex(")") + 1:].split()
        return {"pid": pid, "start": tail[19], "ppid": int(tail[1]),
                "pgid": int(tail[2]), "state": tail[0]}
    except FileNotFoundError:
        return None


def same(identity, current):
    return current is not None and identity["start"] == current["start"]


def live(identity):
    current = stat(identity["pid"])
    return same(identity, current) and current["state"] not in ("Z", "X")


def specific_wait(pid, deadline, identity=None):
    while time.monotonic() < deadline:
        try:
            result, status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            # A killed grandchild can still belong to its exiting parent.
            # Wait for adoption only for the exact fixture identity; never
            # interpret ECHILD as a reap or steal a direct Popen's wait.
            if identity is None or not same(identity, stat(pid)):
                return None
            time.sleep(0.01)
            continue
        if result == pid:
            return status
        time.sleep(0.01)
    raise RuntimeError(f"specific wait deadline: {pid}")


# One executable, with no persistent shell wrapper or real provider. Each
# identity acknowledges F before creating the next role. Descendants inherit
# the backend group unless the explicitly gated escape calls setsid().
PROVIDER = r'''
import json, os, signal, sys, time
from pathlib import Path
d = Path(os.environ["LIFECYCLE_FIXTURE"])
mode = os.environ["LIFECYCLE_MODE"]
custom = os.environ.get("LIFECYCLE_CUSTOM") == "1"
envelope = json.load(sys.stdin) if custom else None
def gate(name):
    until = time.monotonic() + 25
    while not (d / name).exists():
        if time.monotonic() >= until: os._exit(71)
        time.sleep(.01)
def ready(role):
    raw = Path(f"/proc/{os.getpid()}/stat").read_text()
    fields = raw[raw.rindex(")")+1:].split()
    (d / (role+".ready")).write_text(json.dumps({"pid":os.getpid(),"start":fields[19]}))
    gate(role+".ack")
def child(role):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    # Only the timeout child keeps the provider's output pipe open.
    if mode != "C2-timeout-pipe":
        fd = os.open(os.devnull, os.O_RDWR)
        for target in (0,1,2): os.dup2(fd,target)
        if fd > 2: os.close(fd)
    ready(role)
    if role == "A1" and mode != "C1-late-in-group":
        pid = os.fork()
        if pid == 0: child("A2")
    if role == "A1" and mode == "C1-escaped-known":
        gate("escape")
        os.setsid()
        (d / "escaped").write_text("ready")
    while True: time.sleep(.05)
ready("B0" if mode == "B" else "A0")
if mode not in ("B", "C3-reused-identity"):
    if mode == "C1-late-in-group": gate("late")
    pid = os.fork()
    if pid == 0: child("A1")
gate("exit")
if custom:
    print(json.dumps({"success":True,"duration_seconds":0,
        "session_id":envelope["invocation"]["invocation_id"],"returncode":0,
        "stdout_tail":"fixture","stderr_tail":"",
        "adapter_metadata":{"adapter":"lifecycle-fixture","adapter_version":"1.0",
                            "contract_version":1}}), flush=True)
else:
    print("synthetic lifecycle fixture",flush=True)
'''


class Venue:
    def __init__(self, scratch, output, seconds, started):
        self.scratch = scratch
        self.output = output
        self.started = started
        self.final_deadline = self.started + seconds
        self.rescue_deadline = self.final_deadline - 15
        self.case_deadline = min(self.started + 15, self.rescue_deadline)
        self.ledger = {}
        self.events = []
        self.lock = threading.Lock()
        self.threads = []
        self.results = []
        self.report = {"cases": self.results, "events": self.events,
                       "limits": {"processes": 8, "threads": 32, "events": 1024,
                                  "output_bytes": 1048576, "scratch_bytes": 67108864,
                                  "total_seconds": seconds}}
        self.boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        self.sock = None
        self.watchdog_pid = None
        self.sentinels = {}
        self.peaks = {"processes": 1, "threads": 1}

    def event(self, kind, **fields):
        with self.lock:
            require(len(self.events) < 1024, "observer event cap")
            self.events.append({"t": round(time.monotonic() - self.started, 6),
                                "kind": kind, **fields})

    def boundary(self):
        require(time.monotonic() < self.case_deadline, "case action deadline")
        self.budgets()

    def budgets(self):
        current = [v for v in self.ledger.values() if live(v)]
        threads = 0
        for identity in current:
            try:
                with RAW_SCANDIR(f"/proc/{identity['pid']}/task") as it:
                    threads += sum(1 for _ in it)
            except FileNotFoundError:
                require(not same(identity, stat(identity["pid"])), "task enumeration unavailable for live identity")
        self.peaks["processes"] = max(self.peaks["processes"], len(current))
        self.peaks["threads"] = max(self.peaks["threads"], threads)
        require(len(current) <= 8 and threads <= 32, "fixture process/thread cap")

    def wait(self, predicate, message, seconds=None):
        end = self.case_deadline if seconds is None else min(self.case_deadline, time.monotonic() + seconds)
        while time.monotonic() < end:
            if predicate():
                self.boundary()
                return
            self.boundary()
            time.sleep(0.01)
        raise AssertionError(message)

    def send(self, command="ledger"):
        if self.sock is not None:
            packet = {"command": command, "deadline": self.case_deadline,
                      "ledger": self.ledger, "boot": self.boot}
            self.sock.send(json.dumps(packet).encode())

    def add(self, role, identity):
        current = stat(identity["pid"])
        require(same(identity, current), f"fixture identity changed: {role}")
        self.ledger[role] = dict(current)
        self.event("identity", role=role, **current)
        self.send()
        self.budgets()

    def watchdog(self, child_socket):
        os.setsid()
        child_socket.settimeout(0.05)
        ledger = {}
        deadline = self.started + 15
        while time.monotonic() < self.final_deadline - 5:
            try:
                raw = child_socket.recv(16384)
                if not raw:
                    break
                packet = json.loads(raw)
                ledger = packet["ledger"]
                deadline = min(packet["deadline"], self.rescue_deadline)
                if packet["command"] == "close":
                    child_socket.send(b'{"ack":"closed"}')
                    return
                child_socket.send(b'{"ack":"ledger","rescue_ready":true}')
            except socket.timeout:
                pass
            if time.monotonic() >= deadline:
                for role, identity in ledger.items():
                    if role not in ("F", "W", "S", "J") and live(identity):
                        RAW_KILL(identity["pid"], signal.SIGKILL)
                child_socket.send(b'{"ack":"rescue","reason":"deadline"}')
                # F remains the live reaper. W never kills F or adopts work.
                deadline = min(time.monotonic() + 10, self.final_deadline - 5)
        for role, identity in ledger.items():
            if role not in ("F", "W") and live(identity):
                RAW_KILL(identity["pid"], signal.SIGKILL)

    def drain_watchdog(self):
        messages = []
        self.sock.setblocking(False)
        try:
            while True:
                messages.append(json.loads(self.sock.recv(16384)))
        except BlockingIOError:
            pass
        finally:
            self.sock.setblocking(True)
        require(not any(m.get("ack") == "rescue" for m in messages), "watchdog rescue before backend evidence")
        return messages

    def readiness(self):
        require(sys.platform == "linux", "Linux-only venue")
        require(sys.version_info >= (3, 14), "effective Python3.14 required")
        libc = ctypes.CDLL(None, use_errno=True)
        flag = ctypes.c_int()
        require(libc.prctl(36, 1, 0, 0, 0) == 0, "SET_CHILD_SUBREAPER unavailable")
        require(libc.prctl(37, ctypes.byref(flag), 0, 0, 0) == 0 and flag.value == 1,
                "GET_CHILD_SUBREAPER unavailable")
        self.add("F", stat(os.getpid()))
        parent_socket, child_socket = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        pid = os.fork()
        if pid == 0:
            parent_socket.close()
            try:
                self.watchdog(child_socket)
            finally:
                os._exit(0)
        child_socket.close()
        self.sock, self.watchdog_pid = parent_socket, pid
        self.add("W", stat(pid))
        # Prove specific adopted-child wait authority with P/Q before cases.
        read_fd, write_fd = os.pipe()
        release_read, release_write = os.pipe()
        acknowledged_read, acknowledged_write = os.pipe()
        parent_pid = os.fork()
        if parent_pid == 0:
            os.close(read_fd)
            os.close(release_write)
            os.close(acknowledged_write)
            child_pid = os.fork()
            if child_pid == 0:
                os.close(write_fd)
                os.read(release_read, 1)
                os._exit(0)
            os.write(write_fd, json.dumps(stat(child_pid)).encode() + b"\n")
            # P stays alive until F has the exact Q identity in both ledgers.
            os.read(acknowledged_read, 1)
            os._exit(0)
        os.close(write_fd)
        os.close(release_read)
        os.close(acknowledged_read)
        self.add("P", stat(parent_pid))
        import select
        require(select.select([read_fd], [], [], 2)[0], "P/Q probe message missing")
        identity = json.loads(os.read(read_fd, 4096))
        os.close(read_fd)
        self.add("Q", identity)
        os.write(acknowledged_write, b"x")
        os.close(acknowledged_write)
        require(specific_wait(parent_pid, self.case_deadline) is not None, "P wait missing")
        self.wait(lambda: stat(identity["pid"])["ppid"] == os.getpid(), "Q not adopted by F")
        os.write(release_write, b"x")
        os.close(release_write)
        q_status = specific_wait(identity["pid"], self.case_deadline)
        require(q_status is not None and not live(identity), "Q specific wait/liveness proof missing")
        self.event("adoption_readiness", parent=parent_pid, adopted=identity["pid"], status=q_status)
        self.ledger.pop("P")
        self.ledger.pop("Q")
        self.send()
        self.wait(lambda: any(m.get("rescue_ready") is True for m in self.drain_watchdog()),
                  "W rescue-readiness acknowledgement missing", 2)

    def sentinel(self, role):
        path = self.scratch / (role + ".heartbeat")
        pid = os.fork()
        if pid == 0:
            os.setsid()
            counter = 0
            while True:
                pending = path.with_suffix(".pending")
                pending.write_text(str(counter))
                pending.replace(path)
                counter += 1
                time.sleep(0.025)
        self.add(role, stat(pid))
        self.wait(path.exists, f"{role} heartbeat missing", 2)
        self.sentinels[role] = path

    def sentinel_snapshot(self):
        result = {}
        for role, path in self.sentinels.items():
            require(live(self.ledger[role]), f"foreign sentinel {role} killed")
            try:
                value = path.read_text()
                if not value:
                    value = "-1"
                result[role] = int(value)
            except ValueError:
                raise AssertionError("invalid sentinel heartbeat")
        return result

    def cleanup(self, roles, direct=()):
        """Separate fixture rescue. Evidence is captured BEFORE calling this."""
        deadline = min(time.monotonic() + 10, self.final_deadline - 1)
        rescued = []
        for role in reversed(tuple(roles)):
            identity = self.ledger.get(role)
            if identity and live(identity):
                RAW_KILL(identity["pid"], signal.SIGKILL)
                rescued.append(role)
        for proc in direct:
            proc.wait(timeout=max(0.0, deadline - time.monotonic()))
            for pipe in (proc.stdin, proc.stdout, proc.stderr):
                if pipe is not None:
                    pipe.close()
        direct_pids = {proc.pid for proc in direct}
        for role in reversed(tuple(roles)):
            identity = self.ledger.get(role)
            if identity is None:
                continue
            if identity["pid"] not in direct_pids:
                status = specific_wait(identity["pid"], deadline, identity)
                self.event("fixture_specific_wait", role=role, status=status)
            require(not live(identity), f"unsafe residual: {role}")
            # Nonlive alone is insufficient: any surviving matching zombie
            # means F did not establish complete own-child reaping.
            require(not same(identity, stat(identity["pid"])), f"unreaped fixture: {role}")
            self.ledger.pop(role)
        self.event("fixture_rescue", roles=rescued)
        self.send()

    def close(self):
        self.case_deadline = min(time.monotonic() + 10, self.final_deadline - 1)
        failures = []
        try:
            self.cleanup([role for role in self.ledger if role not in ("F", "W")])
            for worker in self.threads:
                worker.join(timeout=max(0.0, self.case_deadline - time.monotonic()))
            require(all(not thread.is_alive() for thread in self.threads), "fixture thread remains")
        except BaseException as exc:
            failures.append(str(exc))
        finally:
            if self.sock is not None:
                try:
                    self.send("close")
                    self.sock.settimeout(max(0.01, self.case_deadline - time.monotonic()))
                    while True:
                        message = json.loads(self.sock.recv(16384))
                        if message.get("ack") == "closed":
                            break
                except BaseException as exc:
                    failures.append(str(exc))
                    identity = self.ledger.get("W")
                    if identity and live(identity):
                        RAW_KILL(identity["pid"], signal.SIGKILL)
                finally:
                    status = specific_wait(self.watchdog_pid, min(self.final_deadline, time.monotonic() + 1))
                    require(status is not None, "W wait missing")
                    require(not same(self.ledger["W"], stat(self.watchdog_pid)), "W remains")
                    self.sock.close()
                    self.sock = None
        require(not failures, "; ".join(failures))
        self.event("venue_settled")


class BackendObserver:
    """Delegating wrappers at existing dependencies; no shipping timing hook."""

    name = "macos-process-group"

    def __init__(self, venue, backend, case):
        self.v, self.backend, self.case = venue, backend, case
        self.running = {}
        self.samples = {}
        self.started = []
        self.finish_entered = threading.Event()
        self.launch_held = threading.Event()
        self.launch_release = threading.Event()
        self.read_entered = threading.Event()
        self.read_release = threading.Event()
        self.arm_read = False
        self.fault = None
        self.fault_hits = 0
        self.rows = 0
        self.scan_attempts = []
        self.read_operations = {}
        self.signal_attempts = []
        self.errors = []
        self.case_dirs = {}

    def probe(self):
        # Delegate the REAL operational probe, accounting its direct helper.
        original_popen = subprocess.Popen
        helpers = []

        def launched(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            helpers.append(process)
            self.v.add("probe", stat(process.pid))
            return process

        subprocess.Popen = launched
        try:
            report = self.backend.probe()
        finally:
            subprocess.Popen = original_popen
        for process in helpers:
            require(process.returncode is not None, "probe helper was not reaped by backend")
            require(stat(process.pid) is None, "probe helper remains")
        self.v.ledger.pop("probe", None)
        self.v.send()
        with RAW_OPEN(f"/proc/{os.getpid()}/task/{os.getpid()}/children") as stream:
            children = [int(value) for value in stream.read(4096).split()]
        known = {row["pid"] for row in self.v.ledger.values()}
        unexpected = [pid for pid in children if pid not in known]
        for pid in unexpected:
            current = stat(pid)
            if current is not None:
                self.v.add(f"probe-own-child-{pid}", current)
        require(not unexpected, "probe left an unaccounted own child; venue refuses")
        return report

    def __getattr__(self, name):
        return getattr(self.backend, name)

    @contextmanager
    def marked(self, operation, owner="A"):
        previous = getattr(TLS, "backend", None)
        TLS.backend = (self, operation, owner)
        try:
            yield
        finally:
            TLS.backend = previous

    def launch(self, pending, spec):
        owner = "B" if pending.request_id.endswith("-B") else "A"
        with self.marked("launch", owner):
            running = self.backend.launch(pending, spec)
        self.running[owner] = running
        proc = running.process
        original_wait = proc.wait

        def observed_wait(*args, **kwargs):
            result = original_wait(*args, **kwargs)
            marker = getattr(TLS, "backend", None)
            operation = marker[1] if marker else "communicate_or_fixture"
            self.v.event("backend_direct_wait", owner=owner, pid=proc.pid, returncode=result, operation=operation)
            return result

        proc.wait = observed_wait
        self.v.event("launch_return", owner=owner, token=running.token, pid=running.root_pid)
        if self.case == "C2-shutdown-bind":
            self.launch_held.set()
            require(self.launch_release.wait(15), "launch-return hold expired")
        return running

    def sample(self, running):
        owner = "B" if self.running.get("B") == running else "A"
        with self.marked("sample", owner):
            sample = self.backend.sample(running)
        self.samples[owner] = self.samples.get(owner, 0) + 1
        self.v.event("factory_scheduled_sample", owner=owner, token=running.token)
        return sample

    def finish(self, running, *args, **kwargs):
        owner = "B" if self.running.get("B") == running else "A"
        self.v.event("finish_enter", owner=owner, token=running.token,
                     root_returncode=running.process.returncode)
        if owner == "A":
            self.finish_entered.set()
        try:
            with self.marked("finish", owner):
                receipt = self.backend.finish(running, *args, **kwargs)
        except Exception as exc:
            self.v.event("finish_error", owner=owner, error=str(exc))
            raise
        self.v.event("finish_return", owner=owner, receipt=dataclasses.asdict(receipt))
        return receipt

    def history_has(self, pid):
        running = self.running.get("A")
        if running is None:
            return False
        session = self.backend._sessions.get(running.token)
        members = session[-1] if isinstance(session, tuple) else getattr(session, "members", {})
        return any(observation.pid == pid for observation in members.values())

    @contextmanager
    def dependencies(self, census_module):
        reader_class = census_module.LinuxProcReader
        original_read = reader_class.read_all
        original_process = reader_class.read_process
        original_identity = reader_class.start_identity
        observer = self

        def read(reader):
            marker = getattr(TLS, "backend", None)
            if marker and marker[0] is observer:
                _, operation, owner = marker
                observer.read_operations[operation] = observer.read_operations.get(operation, 0) + 1
                if observer.case == "C3-reused-identity" and operation == "finish":
                    running = observer.running["A"]
                    return [census_module.ProcessObservation(
                        pid=running.root_pid, ppid=os.getpid(), pgid=running.root_pid,
                        start_identity=running.start_identity + "-replacement", state="S")]
                if observer.arm_read and operation == "sample" and owner == "A":
                    observer.arm_read = False
                    observer.read_entered.set()
                    observer.v.event("sample_read_entered", token=observer.running["A"].token)
                    require(observer.read_release.wait(12), "in-flight read barrier expired")
            return original_read(reader)

        def process(reader, pid):
            marker = getattr(TLS, "backend", None)
            if marker and marker[0] is observer and observer.case == "C3-reused-identity" and marker[1] == "finish":
                rows = read(reader)
                return next((row for row in rows if row.pid == pid), None)
            return original_process(reader, pid)

        def identity(reader, pid):
            marker = getattr(TLS, "backend", None)
            if marker and marker[0] is observer and observer.case == "C3-reused-identity" and marker[1] == "finish":
                result = process(reader, pid)
                return result.start_identity if result else None
            return original_identity(reader, pid)

        def getpgid(pid):
            marker = getattr(TLS, "backend", None)
            if marker and marker[0] is observer and observer.case == "C3-reused-identity" and marker[1] == "finish":
                return observer.running["A"].root_pid
            return RAW_GETPGID(pid)

        def opened(path, *args, **kwargs):
            marker = getattr(TLS, "backend", None)
            if marker and marker[0] is observer and observer.fault:
                target = observer.v.ledger["A1"]["pid"]
                if os.fspath(path) == f"/proc/{target}/stat" and observer.fault == "permission":
                    observer.fault_hits += 1
                    raise PermissionError("fixture exact known identity stat input")
                if observer.fault == "cap" and os.fspath(path).startswith("/proc/90000000"):
                    if os.fspath(path).endswith("/statm"):
                        return io.StringIO("1 1\n")
                    pid = int(os.fspath(path).split("/")[2])
                    fields = ["S", str(os.getpid()), str(pid)] + ["0"] * 16 + [str(pid)] + ["0"] * 8
                    return io.StringIO(f"{pid} (synthetic) " + " ".join(fields))
            return RAW_OPEN(path, *args, **kwargs)

        class Entries:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def __iter__(self):
                pids = [observer.v.ledger["A1"]["pid"]] + list(range(900000001, 900000009))
                attempt = {"rows": 0}
                observer.scan_attempts.append(attempt)
                for pid in pids:
                    observer.rows += 1
                    attempt["rows"] += 1
                    yield SimpleNamespace(name=str(pid), is_dir=lambda: True)

        def scandir(path):
            marker = getattr(TLS, "backend", None)
            if marker and marker[0] is observer and observer.fault == "cap" and os.fspath(path) == "/proc":
                return Entries()
            return RAW_SCANDIR(path)

        def killpg(pgid, sig):
            marker = getattr(TLS, "backend", None)
            if not marker or marker[0] is not observer:
                raise AssertionError("unattributed group signal in fixture")
            owner = marker[2]
            observer.signal_attempts.append({"owner": owner, "pgid": pgid, "signal": int(sig)})
            if observer.case == "C3-reused-identity":
                return  # intercepted BEFORE any forwarding; decision logic only
            running = observer.running[owner]
            require(pgid == running.root_pid, "foreign numeric group target")
            for role in ("S", "J", "F", "W", "B0" if owner == "A" else "A0"):
                identity_row = observer.v.ledger.get(role)
                if identity_row:
                    current = stat(identity_row["pid"])
                    require(not same(identity_row, current) or current["pgid"] != pgid,
                            f"collateral group target: {role}")
            if observer.case == "C5-teardown-error" and marker[1] == "finish":
                raise PermissionError("fixture delegated group-signal failure")
            return RAW_KILLPG(pgid, sig)

        def kill(pid, sig):
            if sig == 0:
                return RAW_KILL(pid, sig)
            require(any(row["pid"] == pid and same(row, stat(pid))
                        for role, row in observer.v.ledger.items() if role in ("A0", "B0")),
                    "foreign direct signal target")
            return RAW_KILL(pid, sig)

        old_cap = getattr(census_module, "_CHECKED_ROW_LIMIT", None)
        reader_class.read_all, reader_class.read_process, reader_class.start_identity = read, process, identity
        builtins.open, os.scandir, os.getpgid = opened, scandir, getpgid
        os.killpg, os.kill = killpg, kill
        try:
            yield
        finally:
            reader_class.read_all, reader_class.read_process, reader_class.start_identity = original_read, original_process, original_identity
            builtins.open, os.scandir, os.getpgid = RAW_OPEN, RAW_SCANDIR, RAW_GETPGID
            os.killpg, os.kill = RAW_KILLPG, RAW_KILL
            if old_cap is not None:
                census_module._CHECKED_ROW_LIMIT = old_cap


def run_case(v, case, api, fault=None):
    from runtime.orchestrator.executors import CustomAdapterExecutor, _run_command
    backend = api.backend()
    observer = BackendObserver(v, backend, case)
    publications, terminals, results, errors = [], [], {}, []
    # Real operational probe occurs before providers/overlap, never bypassed.
    supervisor = api.factory(backend=observer, publisher=lambda receipt: (
        publications.append(receipt), v.event("publish", token=receipt.invocation_kind)))
    require(supervisor.probe().healthy, "process-group backend probe failed")
    original_release = supervisor._admission._release

    def released(lease):
        v.event("lease_release", logical_id=lease.ctx._logical_id)
        original_release(lease)

    supervisor._admission._release = released
    original_finish = supervisor._finish_and_reconcile

    def finish_protocol(ctx):
        v.event("finish_protocol_enter", logical_id=ctx._logical_id)
        return original_finish(ctx)

    supervisor._finish_and_reconcile = finish_protocol
    before_sentinels = v.sentinel_snapshot()
    direct = []
    roles = []
    start_index = len(v.events)
    case_dirs = {}
    late_sample_count = None

    def start(owner="A", mode=None):
        mode = mode or case
        directory = v.scratch / (case + ("-" + fault if fault else "") + "-" + owner)
        directory.mkdir()
        case_dirs[owner] = directory
        environment = {"LIFECYCLE_FIXTURE": str(directory), "LIFECYCLE_MODE": mode,
                       "LIFECYCLE_CUSTOM": "1" if case == "C1-group-custom" else "0"}
        executable = v.scratch / "provider"
        request = api.request(org="synthetic-lifecycle-org", invocation_kind="task",
                              logical_id="synthetic-lifecycle-" + owner,
                              executor_profile="synthetic-lifecycle",
                              on_started=lambda pid: observer.started.append(pid))
        custom = None
        if case == "C1-group-custom":
            custom = CustomAdapterExecutor(
                profile_name="synthetic-lifecycle", adapter_entry_id="lifecycle-fixture",
                adapter_executable=str(executable), adapter_hash=hashlib.sha256(executable.read_bytes()).hexdigest(),
                adapter_version="1.0", adapter_contract_version=1, provider="lifecycle-fixture",
                invocation_context={"agent": "synthetic-fixture-agent", "org": request.org, "invocation_kind": "task"})
            require(custom.verify_launch_ready() is None, "custom executable readiness failure")
            v.event("custom_readiness", sha256=custom._adapter_hash, version="1.0", entry="lifecycle-fixture")
            spec = dataclasses.replace(custom.build_launch_spec(workspace=directory, prompt="synthetic fixture",
                                      session_id="synthetic-session-" + owner), env=environment)
        else:
            spec = api.spec(argv=(str(executable),), cwd=str(directory), env=environment)

        def body(running):
            v.event("body_enter", owner=owner)
            timeout = 1 if case == "C2-timeout-pipe" else 12
            if custom:
                result = custom.run(directory, "synthetic fixture", session_id="synthetic-session-" + owner,
                                    timeout_seconds=timeout, running=running)
            else:
                result = _run_command([str(executable)], directory, "synthetic-session-" + owner,
                                      timeout, input_text="synthetic fixture", provider="lifecycle-fixture",
                                      running=running, throttle_backoff_seconds=())
            v.event("communicate_result", owner=owner, success=result.success,
                    failure_category=result.failure_category, error=result.error)
            return api.result(success=result.success, duration_seconds=result.duration_seconds,
                              returncode=result.returncode, error=result.error,
                              timed_out=result.failure_category == "provider_timeout", payload=result)

        def target():
            try:
                results[owner] = supervisor.run(request, launch_spec=spec, launch_body=body,
                    pre_launch_validator=(lambda: require(custom.verify_launch_ready() is None,
                                                         "custom prelaunch readiness")) if custom else None,
                    on_terminal=lambda outcome: (terminals.append(owner), v.event("terminal_hook", owner=owner)),
                    timeout=3, allow_retries=False)
            except BaseException as exc:
                errors.append(f"{type(exc).__name__}: {exc}")

        thread = threading.Thread(target=target, name="lifecycle-run-" + owner)
        v.threads.append(thread)
        thread.start()
        return request, thread

    def adopt_role(role, owner="A"):
        directory = case_dirs[owner]
        ready = directory / (role + ".ready")
        v.wait(ready.exists, f"{role} readiness missing")
        v.add(role, json.loads(ready.read_text()))
        roles.append(role)
        (directory / (role + ".ack")).touch()

    record = {"case": case, "input": fault, "status": "MISSING", "assertions": []}
    try:
        with observer.dependencies(api.census):
            request, thread = start()
            v.wait(lambda: "A" in observer.running, "real launch handle missing")
            direct.append(observer.running["A"].process)
            adopt_role("A0")
            if case == "C1-late-in-group":
                v.wait(lambda: observer.samples.get("A", 0) > 0, "factory sample missing before late child")
                late_sample_count = observer.samples["A"]
                (case_dirs["A"] / "late").touch()
                adopt_role("A1")
            elif case != "C3-reused-identity":
                adopt_role("A1")
                adopt_role("A2")
            if case not in ("C2-shutdown-bind", "C1-late-in-group", "C3-reused-identity"):
                # Missing factory wiring is a product assertion on the pinned
                # baseline, not a fixture-readiness failure or manual sample.
                if api.mode == "baseline":
                    until = min(time.monotonic() + 2.2, v.case_deadline)
                    while time.monotonic() < until and not observer.history_has(v.ledger["A1"]["pid"]):
                        v.boundary()
                        time.sleep(.01)
                else:
                    v.wait(lambda: observer.history_has(v.ledger["A1"]["pid"]), "owned child never sampled")
            if case == "C1-escaped-known":
                sample_count = observer.samples.get("A", 0)
                (case_dirs["A"] / "escape").touch()
                v.wait((case_dirs["A"] / "escaped").exists, "setsid did not run")
                if api.mode != "baseline":
                    v.wait(lambda: observer.samples.get("A", 0) > sample_count, "no factory sample after escape")
            if case == "C2-shutdown-bind":
                require(observer.launch_held.is_set(), "launch not held before bind")
                contexts = supervisor._admission.ownerships_snapshot()
                require(len(contexts) == 1 and contexts[0].running() is None, "context already bound")
                supervisor.shutdown()
                require(contexts[0].terminal_reason().value == "shutdown" and contexts[0].running() is None,
                        "SHUTDOWN not frozen while unbound")
                v.event("frozen_shutdown_unbound", token=observer.running["A"].token)
                observer.launch_release.set()
            elif case == "C4-owner-isolation":
                observer.arm_read = True
                v.wait(observer.read_entered.is_set, "factory A read not in flight after capture")
                b_request, b_thread = start("B", "B")
                v.wait(lambda: "B" in observer.running, "distinct B admission missing")
                direct.append(observer.running["B"].process)
                adopt_role("B0", "B")
                require(observer.running["A"].token != observer.running["B"].token, "token alias")
                b_context = next(c for c in supervisor._admission.ownerships_snapshot()
                                 if c.running() == observer.running["B"])
                b_samples = b_context.samples_snapshot()
                control = threading.Thread(target=request.cancellation.cancel, name="lifecycle-cancel-A")
                v.threads.append(control)
                control.start()
                # Backend entry may serialize behind the supervisor sample
                # lock. A frozen reason and no publication/release is valid.
                v.wait(lambda: any(c.terminal_reason() is not None for c in
                                  supervisor._admission.ownerships_snapshot() if c is not b_context),
                       "A cancellation not frozen")
                terminal_before = results.get("A")
                frozen = json.dumps(dataclasses.asdict(terminal_before.receipt), sort_keys=True) if terminal_before and terminal_before.receipt else None
                if terminal_before is None:
                    require(not publications and not terminals and supervisor.active_count() == 2,
                            "premature final evidence/release with unsettled read")
                    v.event("sample_finish_serialized", terminal_frozen=True, published=False, active=2)
                observer.read_release.set()
                v.wait(lambda: not control.is_alive() and not thread.is_alive(), "A did not settle after read release")
                control.join()
                require(observer.running["A"].token not in backend._sessions, "retired A resurrected")
                if frozen:
                    require(json.dumps(dataclasses.asdict(results["A"].receipt), sort_keys=True) == frozen,
                            "terminal A evidence changed after delayed read")
                require(b_context in supervisor._admission.ownerships_snapshot()
                        and b_context.running() == observer.running["B"] and "B" not in terminals,
                        "A changed/released B context")
                require(len(b_context.samples_snapshot()) >= len(b_samples), "B sample/account loss")
                require(live(v.ledger["B0"]), "A killed B")
                (case_dirs["B"] / "exit").touch()
                v.wait(lambda: not b_thread.is_alive(), "B independent finish missing")
                b_thread.join()
            elif case == "C2-cancel":
                control = threading.Thread(target=request.cancellation.cancel, name="lifecycle-cancel")
                v.threads.append(control)
                control.start()
                v.wait(lambda: not control.is_alive(), "cancel cleanup did not settle")
                control.join()
            else:
                if fault:
                    observer.fault = fault
                    if fault == "cap":
                        require(hasattr(api.census, "_CHECKED_ROW_LIMIT"), "checked private cap unavailable")
                        api.census._CHECKED_ROW_LIMIT = 8
                    else:
                        v.wait(lambda: observer.fault_hits > 0, "exact low-level PermissionError not reached")
                    require(live(v.ledger["A1"]), "fault oracle child not live")
                (case_dirs["A"] / "exit").touch()
            v.wait(lambda: not thread.is_alive(), "supervisor run did not settle within action budget")
            thread.join()
            require(not errors, f"runtime/fixture errors: {errors}")
            require("A" in results, "missing actual outcome")
            outcome = results["A"]
            receipt = outcome.receipt
            evidence = v.events[start_index:]
            record.update({"terminal_reason": outcome.terminal_reason.value,
                           "cleanup_error": outcome.cleanup_error,
                           "receipt": dataclasses.asdict(receipt) if receipt else None,
                           "residue": supervisor.health_snapshot()["residue"],
                           "signals": observer.signal_attempts,
                           "fault_hits": observer.fault_hits, "scan_rows": observer.rows,
                           "scan_attempts": observer.scan_attempts,
                           "known_live_before_rescue": {role: live(v.ledger[role]) for role in roles},
                           "factory_samples": observer.samples})
            # Freeze backend result BEFORE separate fixture rescue/reaping.
            v.event("backend_evidence_frozen", case=case, input=fault)
            require(sum(e["kind"] == "finish_enter" and e["owner"] == "A" for e in evidence) == 1,
                    "finish not exactly once")
            require(sum(e["kind"] == "terminal_hook" and e["owner"] == "A" for e in evidence) == 1,
                    "terminal hook not exactly once")
            require(supervisor.active_count() == 0 and supervisor._admission.released_total() == len(direct),
                    "lease not independently released exactly once")
            waits = [i for i,e in enumerate(evidence) if e["kind"] == "backend_direct_wait" and e["owner"] == "A"]
            hooks = [i for i,e in enumerate(evidence) if e["kind"] == "terminal_hook" and e["owner"] == "A"]
            releases = [i for i,e in enumerate(evidence) if e["kind"] == "lease_release" and e["logical_id"].endswith("-A")]
            require(waits and max(waits) < hooks[0] < releases[0], "direct wait/terminal/release order")
            require(not same(v.ledger["A0"], stat(v.ledger["A0"]["pid"])), "backend direct root unreaped")
            record["assertions"].append("one finish/direct wait/terminal hook/lease release in order")
            if case == "C1-late-in-group":
                require(observer.samples.get("A") == late_sample_count, "late child was not after the last periodic sample")
                require(observer.read_operations.get("finish", 0) > 0, "fresh finish observation missing")
                remaining = [role for role in roles if live(v.ledger[role])]
                require((receipt is not None and not remaining and receipt.quiescent)
                        or (outcome.cleanup_error and not supervisor._residue.evaluate().admit)
                        or (receipt is not None and not receipt.quiescent),
                        "late child falsely clean without fresh authenticated group witness")
                record["assertions"].append("fresh terminal observation cleans late child or retains explicit uncertainty")
            elif fault or case in ("C3-reused-identity", "C5-teardown-error"):
                if case == "C3-reused-identity":
                    require(not observer.signal_attempts, "replacement numeric group received signal attempt")
                require(outcome.cleanup_error or (receipt and not receipt.quiescent), "fault became clean")
                require(not supervisor._residue.evaluate().admit, "unknown cleanup did not close gate")
                require(outcome.terminal_reason.value == "success", "cleanup replaced provider success reason")
                require(len(publications) == (1 if receipt else 0), "fabricated/duplicate publication")
                if fault == "permission":
                    require(observer.fault_hits > 0 and live(v.ledger["A1"]), "permission fault not proven")
                if fault == "cap":
                    require(observer.scan_attempts and all(a["rows"] == 9 for a in observer.scan_attempts)
                            and live(v.ledger["A1"]), "row9/private cap8 not exercised")
                record["assertions"].append("real fault/reuse refused clean outcome and closed gate")
            elif case == "C1-escaped-known":
                identity_row = v.ledger["A1"]
                require(observer.samples.get("A", 0) >= 2, "factory sampler missing")
                require(receipt is not None and not receipt.quiescent
                        and any(s.pid == identity_row["pid"] and s.start_identity == identity_row["start"]
                                for s in receipt.survivors) and live(identity_row), "known escaped identity forgotten")
                require(any(s.pid == identity_row["pid"] for s in supervisor._residue.census()), "escaped identity not charged")
                record["assertions"].append("factory sampling retains escaped live identity in receipt and residue")
            else:
                require(receipt is not None, "missing cleanup receipt")
                remaining = [role for role in roles if live(v.ledger[role])]
                require(not remaining or not receipt.quiescent, "live owned child falsely clean")
                if case != "C1-late-in-group":
                    require(not remaining and receipt.quiescent, "owned group cleanup incomplete")
                require(len(publications) == len(direct), "receipt not published once per invocation")
                if case == "C2-shutdown-bind":
                    require(outcome.terminal_reason.value == "shutdown" and not observer.started
                            and not observer.samples and not any(e["kind"] == "body_enter" for e in evidence),
                            "bind gate did not skip body/on_started/sampler")
                    require(any(e["kind"] == "backend_direct_wait" and e["operation"] == "finish" for e in evidence),
                            "backend wait missing from skipped-body cut")
                if case == "C2-cancel":
                    require(outcome.terminal_reason.value == "cancelled", "cancel winner lost")
                    require(any(s["signal"] == signal.SIGKILL for s in observer.signal_attempts), "KILL escalation missing")
                if case == "C2-timeout-pipe":
                    require(outcome.terminal_reason.value == "timeout" and outcome.payload.failure_category == "provider_timeout",
                            "real pipe-holder communicate timeout lost")
                if case == "C3-root-gone":
                    require(any(e["kind"] == "finish_enter" and e["root_returncode"] is not None for e in evidence),
                            "root was not already waited before finish")
                record["assertions"].append("real contained communication/group cleanup and case-specific final transition")
            after = v.sentinel_snapshot()
            require(all(after[r] > before_sentinels[r] for r in before_sentinels), "sentinel heartbeat did not advance")
            v.drain_watchdog()
            record["status"] = "PASS"
    except Exception as exc:
        record.update(status="FAIL", error=f"{type(exc).__name__}: {exc}")
        v.event("case_failure", case=case, input=fault, error=record["error"])
    finally:
        observer.read_release.set()
        observer.launch_release.set()
        # Controls/pipes settle after targeted fixture rescue, without claiming
        # that rescue was the missing backend wait/cleanup.
        for owner, running in observer.running.items():
            if running.process not in direct:
                direct.append(running.process)
            role = "B0" if owner == "B" else "A0"
            if role not in v.ledger and stat(running.root_pid) is not None:
                v.add(role, stat(running.root_pid))
                roles.append(role)
        v.cleanup(roles, direct)
        for worker in v.threads:
            worker.join(timeout=max(0.0, min(v.case_deadline + 10, v.final_deadline - 1) - time.monotonic()))
        require(all(not worker.is_alive() for worker in v.threads), "unsafe thread remains after rescue")
    return record


def main():
    invocation_started = time.monotonic()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--scratch-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=("baseline", "candidate"), required=True)
    args = parser.parse_args()
    source = args.source_root.resolve(strict=True)
    source_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    require(not subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=source, text=True),
            "verification source must have a clean tracked immutable head")
    if args.mode == "baseline":
        require(source_head == "29d743a26ffadea6c7ec7847537db920f7ffe1f3", "baseline must be immutable accepted design source")
    sys.path.insert(0, str(source))
    from runtime.orchestrator.host_supervisor import AdmissionRequest, LaunchResult, build_default_host_supervisor
    from runtime.platform import process_census
    from runtime.platform.macos_process_group import MacOSProcessGroupBackend
    from runtime.platform.session_backend import LaunchSpec
    api = SimpleNamespace(request=AdmissionRequest, result=LaunchResult, spec=LaunchSpec,
                          factory=build_default_host_supervisor, backend=MacOSProcessGroupBackend,
                          census=process_census, mode=args.mode)
    require(args.scratch_root.is_absolute() and args.output.is_absolute(), "absolute scratch/output required")
    args.scratch_root.mkdir(parents=True, exist_ok=True)
    import tempfile
    scratch = Path(tempfile.mkdtemp(prefix="lifecycle-", dir=args.scratch_root))
    v = Venue(scratch, args.output, 90 if args.mode == "baseline" else 390, invocation_started)
    v.report.update({"mode": args.mode, "source": str(source),
                     "head": source_head,
                     "tree": subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=source, text=True).strip(),
                     "python": sys.version, "python_executable": sys.executable,
                     "verifier_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                     "source_sha256": {name: hashlib.sha256((source / name).read_bytes()).hexdigest()
                         for name in ("runtime/orchestrator/host_supervisor.py", "runtime/platform/macos_process_group.py",
                                      "runtime/platform/process_census.py")},
                     "native_journal": "SOURCE-MAPPED ONLY; UNPROVEN", "historical_ownership": "UNPROVEN"})
    try:
        v.readiness()
        executable = scratch / "provider"
        executable.write_text("#!" + sys.executable + "\n" + PROVIDER)
        executable.chmod(0o700)
        v.sentinel("S")
        v.sentinel("J")
        v.report["readiness"] = "PASS"
        cases = BASELINE_CASES if args.mode == "baseline" else CASES
        for case in cases:
            v.case_deadline = min(time.monotonic() + 20, v.rescue_deadline)
            if case == "C5-unreadable":
                inputs = [run_case(v, case, api, fault) for fault in ("permission", "cap")]
                v.results.append({"case": case, "status": "PASS" if all(r["status"] == "PASS" for r in inputs) else "FAIL",
                                  "inputs": inputs})
            else:
                v.results.append(run_case(v, case, api))
            # Baseline expected product failures do not authorize retries.
    except BaseException as exc:
        v.report.update(fatal=f"{type(exc).__name__}: {exc}", trace=traceback.format_exc(limit=6))
    finally:
        try:
            v.close()
            v.report["fixture_cleanup"] = "SETTLED"
        except BaseException as exc:
            v.report["fixture_cleanup"] = f"RESIDUAL/UNKNOWN: {exc}"
        v.report.update(elapsed_seconds=round(time.monotonic() - v.started, 6), peaks=v.peaks,
                        scratch=str(scratch), boot_id=v.boot)
        size = sum(p.stat().st_size for p in scratch.rglob("*") if p.is_file())
        v.report["scratch_bytes"] = size
        require(size <= 67108864, "scratch cap exceeded")
        expected = len(BASELINE_CASES if args.mode == "baseline" else CASES)
        v.report["all_cases_present"] = len(v.results) == expected
        v.report["status"] = "PASS" if (len(v.results) == expected and all(r["status"] == "PASS" for r in v.results)
            and v.report.get("fixture_cleanup") == "SETTLED" and "fatal" not in v.report) else "FAIL"
        encoded = json.dumps(v.report, indent=2) + "\n"
        require(len(encoded.encode()) <= 1048576, "observer output cap exceeded")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded)
        print(json.dumps({"status": v.report["status"], "output": str(args.output),
                          "cases": [{"case": r["case"], "status": r["status"]} for r in v.results],
                          "cleanup": v.report["fixture_cleanup"], "elapsed_seconds": v.report["elapsed_seconds"]}))
    return 0 if v.report["status"] == "PASS" else 3


if __name__ == "__main__":
    raise SystemExit(main())
