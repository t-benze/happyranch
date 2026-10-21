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
import dis
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
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace

RAW_OPEN = builtins.open
RAW_KILL = os.kill
RAW_KILLPG = os.killpg
RAW_SCANDIR = os.scandir
RAW_GETPGID = os.getpgid
TLS = threading.local()
CASES = (
    "C1-group-builtin", "C1-group-custom", "C1-escaped-known", "C1-late-in-group",
    "C2-cancel", "C2-cancel-publish-error", "C2-timeout-pipe", "C2-shutdown-bind", "C3-root-gone",
    "C3-reused-identity", "C4-owner-isolation", "C5-unreadable", "C5-teardown-error",
)
BASELINE_CASES = ("C1-escaped-known", "C3-reused-identity")
F1_CASES = ("C2-cancel", "C2-cancel-publish-error")
SELECTIONS = {
    "F1": F1_CASES,
    "B": BASELINE_CASES,
    "H": ("C1-escaped-known",),
    "K": ("C1-group-builtin", "C1-group-custom", "C2-cancel", "C2-timeout-pipe", "C3-root-gone"),
    "W": ("C2-shutdown-bind",), "T": ("C2-timeout-pipe",),
    "C": ("C4-owner-isolation",), "R": ("C5-unreadable",),
    "E": ("C5-teardown-error",), "L": ("C1-late-in-group",),
    "S": ("C5-unreadable",), "U": ("C1-group-builtin",),
}


class ContractMismatch(AssertionError):
    def __init__(self, message, observed, expected, explicit_values):
        super().__init__(message)
        self.assertion = message
        self.observed = observed
        self.expected = expected
        self.explicit_values = explicit_values


class FinalizationObserver:
    """Bounded, observation-only tracing in the two existing F1 workers.

    Calls establish identities/opportunity, never the external assertion. No
    target call, IPC or test gate runs under the observer lock. Observer errors
    are latched rather than thrown into the subject's callback/error handling.
    """

    def __init__(self, venue, backend_observer, supervisor, publisher):
        from runtime.orchestrator.host_supervisor import _AttemptContext
        self.v, self.backend = venue, backend_observer
        self.accountant = supervisor._residue
        self.codes = {_AttemptContext.finalize_once.__code__: "finalize",
                      threading.Condition.wait.__code__: "wait",
                      threading.Condition.notify_all.__code__: "notify",
                      type(self.accountant).account.__code__: "account",
                      publisher.__code__: "publisher"}
        self.attempt = self.condition = None
        self.frames, self.workers = {}, {}
        self.lock = threading.RLock()
        self.errors = []
        self.callback = self.trace
        self.count = 0

    def latch(self, error):
        # Bounded first error; the trace never delegates to raising Venue.event.
        if not self.errors:
            self.errors.append(str(error)[:256])

    def emit(self, **fields):
        event = {"t": round(time.monotonic() - self.v.started, 6),
                 "kind": "F1_trace", **self.v.scope, **fields}
        with self.v.lock:
            if self.count >= 256 or len(self.v.events) >= 900:
                self.latch("F1 trace retention cap; no observer credit")
                return
            self.v.events.append(event)
            self.count += 1

    def bind(self, attempt):
        conditions = [value for value in vars(attempt).values()
                      if isinstance(value, threading.Condition)]
        require(self.attempt is None and len(conditions) == 1,
                "F1 attempt lacks one actual condition")
        self.attempt, self.condition = attempt, conditions[0]

    @staticmethod
    def argument(frame, position):
        # Loaded parameter positions, with no proposed state-field dependency.
        return frame.f_locals[frame.f_code.co_varnames[position]]

    def trace(self, frame, event, arg):
        try:
            with self.lock:
                return self.observe_trace(frame, event, arg)
        except BaseException as exc:
            self.latch(f"F1 observer synchronization failure: {type(exc).__name__}: {exc}")
            return self.callback

    def observe_trace(self, frame, event, arg):
        try:
            target = self.codes.get(frame.f_code)
            if target is None:
                return None
            tid = threading.get_ident()
            key = (tid, id(frame))
            if event == "call":
                if self.attempt is None or tid not in self.workers:
                    return None
                parent = frame.f_back
                parent_state = self.frames.get((tid, id(parent)))
                if target == "finalize":
                    if self.argument(frame, 0) is not self.attempt:
                        return None
                    # Ignore synchronous claimed reentry on the owner's stack.
                    if any(k[0] == tid and state["target"] == "finalize"
                           for k, state in self.frames.items()):
                        return None
                    if self.argument(frame, 1) is not self.accountant:
                        self.latch("F1 finalizer accountant identity mismatch")
                        return None
                else:
                    if (parent_state is None or parent_state["frame"] is not parent
                            or parent_state["target"] != "finalize"):
                        return None
                    if target in ("wait", "notify"):
                        if self.argument(frame, 0) is not self.condition:
                            return None
                    elif target == "account":
                        if (self.argument(frame, 0) is not self.accountant
                                or self.argument(frame, 1) is not self.backend.final_receipt):
                            self.latch("F1 real accountant/receipt identity mismatch")
                            return None
                    elif self.argument(frame, 0) is not self.backend.final_receipt:
                        self.latch("F1 publisher receipt identity mismatch")
                        return None
                if len(self.frames) >= 16:
                    self.latch("F1 active frame cap")
                    return None
                frame.f_trace_lines = False
                frame.f_trace_opcodes = False
                state = {"frame": frame, "target": target,
                         "worker": self.workers[tid]["role"], "exception": None}
                self.frames[key] = state
                self.emit(target=target, phase="call", worker=state["worker"],
                          thread=tid, frame=id(frame), caller=id(parent),
                          attempt=id(self.attempt), condition=id(self.condition),
                          accountant=id(self.accountant), receipt_identity=id(self.backend.final_receipt))
            else:
                state = self.frames.get(key)
                if state is None or state["frame"] is not frame:
                    return None
                if event == "exception":
                    exception = arg[1]
                    state["exception"] = id(exception)
                    self.emit(target=target, phase="exception", worker=state["worker"],
                              thread=tid, frame=id(frame), exception_identity=id(exception),
                              exception_type=type(exception).__name__, message=str(exception)[:128])
                elif event == "return":
                    # An exceptional unwind also sends return(None). Inspect
                    # the actual return opcode, not that ambiguous value or a
                    # source line. Caught exceptions can still return normally.
                    opcode = dis.opname[frame.f_code.co_code[frame.f_lasti]]
                    normal = opcode in ("RETURN_VALUE", "RETURN_CONST")
                    if not normal and state["exception"] is None:
                        self.latch("F1 unwind lacks its exception event")
                    self.emit(target=target, phase="return" if normal else "unwind",
                              worker=state["worker"], thread=tid, frame=id(frame),
                              opcode=opcode, exception_identity=state["exception"],
                              receipt_identity=id(self.backend.final_receipt))
                    del self.frames[key]
            return self.callback
        except BaseException as exc:
            self.latch(f"F1 observer failure: {type(exc).__name__}: {exc}")
            return self.callback

    @contextmanager
    def worker(self, role):
        previous = sys.gettrace()
        require(previous is None, "F1 refuses prior worker trace ownership")
        tid = threading.get_ident()
        with self.lock:
            self.workers[tid] = {"role": role, "thread": tid, "previous": None,
                                 "owned": False, "restored": False}
        try:
            sys.settrace(self.callback)
            self.workers[tid]["owned"] = sys.gettrace() is self.callback
            require(self.workers[tid]["owned"], "F1 trace installation not owned")
            yield
        finally:
            if sys.gettrace() is not self.callback:
                self.latch("F1 trace ownership replaced")
            sys.settrace(previous)
            self.workers[tid]["restored"] = sys.gettrace() is previous
            if not self.workers[tid]["restored"]:
                self.latch("F1 trace restoration failed")

    def snapshot(self):
        with self.lock, self.v.lock:
            return {"errors": list(self.errors), "workers": [dict(w) for w in self.workers.values()],
                    "codes": {kind: {"identity": id(code), "filename": code.co_filename,
                                     "qualname": code.co_qualname} for code, kind in self.codes.items()},
                    "active_frames": [{"thread": k[0], "frame": k[1], "target": s["target"]}
                                      for k, s in self.frames.items()],
                    "attempt": id(self.attempt), "condition": id(self.condition),
                    "accountant": id(self.accountant), "receipt_identity": id(self.backend.final_receipt),
                    "events": [dict(e) for e in self.v.events
                               if e["kind"] == "F1_trace" and e["generation"] == self.v.scope["generation"]]}


UNRECORDED = object()


def require(value, message, *, observed=UNRECORDED, expected=UNRECORDED):
    if not value:
        explicit = observed is not UNRECORDED and expected is not UNRECORDED
        raise ContractMismatch(message, bool(value) if observed is UNRECORDED else observed,
                               True if expected is UNRECORDED else expected, explicit)


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
        self.scope = {"case": "readiness", "input": None, "generation": 0}
        self.command_sequence = 0
        self.boundary_settled = True
        self.watchdog_rescues = []
        self.safety_violations = []
        self.stopped = False
        self.exchange_deadline = None
        self.diagnostic = None
        self.heartbeat_fault_armed = False
        self.heartbeat_fault_applied = False
        self.report.update(watchdog_rescues=self.watchdog_rescues,
                           safety_violations=self.safety_violations)

    def event(self, kind, **fields):
        with self.lock:
            require(len(self.events) < 1024, "observer event cap")
            self.events.append({"t": round(time.monotonic() - self.started, 6),
                                "kind": kind, **self.scope, **fields})

    def safety_violation(self, message, **target):
        # Latch BEFORE raising: product exception handling cannot erase this.
        with self.lock:
            violation = {**self.scope, "t": time.monotonic() - self.started,
                         "message": message, **target}
            self.safety_violations.append(violation)
            self.stopped = True
        self.event("fixture_safety_violation", violation=violation)
        raise AssertionError(message)

    def boundary(self):
        require(not self.stopped, "fixture stopped; no further action/inputs")
        require(time.monotonic() < self.case_deadline, "case action deadline")
        if self.sentinels:
            self.sentinel_snapshot(tuple(self.sentinels), record=False)
        self.budgets()

    def observe(self, pid):
        try:
            return stat(pid)  # /proc only: never poll/wait/reap a direct child
        except BaseException as exc:
            self.safety_violation("fixture identity observation unavailable", pid=pid,
                                  error=f"{type(exc).__name__}: {exc}")

    def is_live(self, identity):
        current = self.observe(identity["pid"])
        return same(identity, current) and current["state"] not in ("Z", "X")

    def budgets(self):
        try:
            current = [v for v in self.ledger.values() if self.is_live(v)]
            threads = 0
            for identity in current:
                try:
                    with RAW_SCANDIR(f"/proc/{identity['pid']}/task") as it:
                        threads += sum(1 for _ in it)
                except FileNotFoundError:
                    require(not same(identity, self.observe(identity["pid"])), "task enumeration unavailable for live identity")
            self.peaks["processes"] = max(self.peaks["processes"], len(current))
            self.peaks["threads"] = max(self.peaks["threads"], threads)
            require(len(current) <= 8 and threads <= 32, "fixture process/thread cap")
        except BaseException as exc:
            self.safety_violation("fixture process/thread observation or budget unsafe",
                                  error=f"{type(exc).__name__}: {exc}")

    def wait(self, predicate, message, seconds=None):
        end = self.case_deadline if seconds is None else min(self.case_deadline, time.monotonic() + seconds)
        while time.monotonic() < end:
            if predicate():
                self.boundary()
                return
            self.boundary()
            time.sleep(0.01)
        raise AssertionError(message)

    def send(self, command="ledger", *, rescue_deadline=None):
        """One outstanding command; every reply is retained and attributed."""
        if self.sock is None:
            return
        if rescue_deadline is not None:
            require(self.diagnostic == "U" and command == "ledger"
                    and rescue_deadline <= time.monotonic() < self.case_deadline,
                    "only explicit U may supply an earlier in-action W deadline")
        self.command_sequence += 1
        sequence = self.command_sequence
        scope = dict(self.scope)
        deadline = self.case_deadline if rescue_deadline is None else rescue_deadline
        packet = {"command": command, "sequence": sequence, "scope": scope,
                  "deadline": deadline, "ledger": self.ledger, "boot": self.boot}
        self.event("watchdog_command", command=command, sequence=sequence,
                   deadline=deadline, diagnostic=self.diagnostic)
        # Shared action/cleanup deadline, not an extra allowance per message.
        end = min(time.monotonic() + 1, self.case_deadline + (10 if command != "begin" else 0),
                  self.final_deadline - 1)
        if self.exchange_deadline is not None:
            end = min(end, self.exchange_deadline)
        try:
            self.sock.settimeout(max(0.001, end - time.monotonic()))
            self.sock.send(json.dumps(packet).encode())
            while time.monotonic() < end:
                self.sock.settimeout(max(0.001, end - time.monotonic()))
                raw = self.sock.recv(16384)
                require(raw, "W closed without matching acknowledgement")
                reply = json.loads(raw)
                # No drain/discard: rescue from the preceding command still
                # belongs to this exact generation and is persisted first.
                self.event("watchdog_reply", reply=reply)
                require(reply["scope"] == scope and reply["boot"] == self.boot,
                        "W scope/boot mismatch")
                if reply["ack"] == "rescue":
                    require(reply["sequence"] in (sequence - 1, sequence), "unattributed W rescue sequence")
                    self.watchdog_rescues.append(reply)
                    continue
                require(reply["sequence"] == sequence and reply["ack"] == command,
                        "W command acknowledgement mismatch")
                return reply
            raise AssertionError("W acknowledgement deadline")
        except BaseException:
            self.stopped = True
            raise

    def begin_case(self, case, fault):
        require(self.boundary_settled and not self.stopped, "preceding boundary unsettled/unsafe")
        self.acceptance_started_t = time.monotonic() - self.started
        # Refuse before any next-case backend probe or second C5 input.
        self.sentinel_snapshot()
        self.scope = {"case": case, "input": fault, "generation": self.scope["generation"] + 1}
        self.boundary_settled = False
        reply = self.send("begin")
        self.acceptance_begin_sequence = reply["sequence"]

    def settle_case(self):
        self.send("settle")  # disarms this deadline before another generation
        self.boundary_settled = True

    def add(self, role, identity):
        current = self.observe(identity["pid"])
        require(same(identity, current), f"fixture identity changed: {role}")
        self.ledger[role] = dict(current)
        self.event("identity", role=role, **current)
        self.send()
        self.budgets()

    def watchdog(self, child_socket):
        os.setsid()
        child_socket.settimeout(0.05)
        ledger, scope, sequence = {}, dict(self.scope), 0
        deadline, active = self.started + 15, True

        def reply(kind, **fields):
            child_socket.send(json.dumps({"ack": kind, "scope": scope, "sequence": sequence,
                                          "boot": self.boot, "t": time.monotonic() - self.started,
                                          **fields}).encode())

        def rescue(reason, include_sentinels=False):
            decision_t = time.monotonic() - self.started
            actions = []
            for role, identity in ledger.items():
                if role in (("F", "W") if include_sentinels else ("F", "W", "S", "J")):
                    continue
                action = {"role": role, "identity": identity, "boot": self.boot,
                          "decision_t": time.monotonic() - self.started, "signal": int(signal.SIGKILL)}
                try:
                    current = stat(identity["pid"])
                    action["observed"] = current
                    if same(identity, current) and current["state"] not in ("Z", "X"):
                        action["action_t"] = time.monotonic() - self.started
                        RAW_KILL(identity["pid"], signal.SIGKILL)
                        action["outcome"] = "sent"
                    else:
                        action["outcome"] = "identity-absent-or-nonlive; no signal"
                except BaseException as exc:
                    action["outcome"] = f"{type(exc).__name__}: {exc}"
                actions.append(action)
            reply("rescue", reason=reason, decision_t=decision_t, actions=actions)

        while time.monotonic() < self.final_deadline - 5:
            try:
                raw = child_socket.recv(16384)
                if not raw:
                    break
                packet = json.loads(raw)
                command = packet["command"]
                require(packet["boot"] == self.boot and packet["sequence"] == sequence + 1,
                        "W nonsequential command/boot")
                if command == "begin":
                    require(not active and packet["scope"]["generation"] == scope["generation"] + 1,
                            "W preceding boundary not settled")
                else:
                    require(packet["scope"] == scope, "W foreign boundary command")
                ledger, scope, sequence = packet["ledger"], packet["scope"], packet["sequence"]
                if command == "close":
                    reply("close")
                    return
                if command == "settle":
                    active = False
                elif command == "begin":
                    active = True
                deadline = min(packet["deadline"], self.rescue_deadline)
                reply(command, rescue_ready=True)
            except socket.timeout:
                pass
            if (active and time.monotonic() >= deadline) or time.monotonic() >= self.rescue_deadline:
                rescue("action-deadline" if time.monotonic() < self.rescue_deadline else "global-deadline")
                active = False  # exactly one rescue per boundary, no stale rearm
                if time.monotonic() >= self.rescue_deadline:
                    return
        rescue("watchdog-final", include_sentinels=True)
        # F remains the sole reaper. No W wait/kill authority over F.

    def check_watchdog(self, observed_through_t):
        reply = self.send()  # orders all earlier W decisions before this acknowledgement
        rescues = [r for r in self.watchdog_rescues if r["scope"] == self.scope]
        self.event("acceptance_interval", started_t=self.acceptance_started_t,
                   observed_through_t=observed_through_t,
                   begin_sequence=self.acceptance_begin_sequence, check_sequence=reply["sequence"],
                   rescues=rescues)
        require(not any(r["decision_t"] <= observed_through_t for r in rescues),
                "matching watchdog rescue before last acceptance observation")
        return rescues

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
        require(self.send()["rescue_ready"] is True, "W rescue-readiness acknowledgement missing")
        self.settle_case()

    def sentinel(self, role):
        path = self.scratch / (role + ".heartbeat")
        end = min(self.case_deadline, time.monotonic() + 2)
        try:
            pid = os.fork()
            if pid == 0:
                try:
                    os.setsid()
                    counter = 0
                    while True:
                        pending = path.with_suffix(".pending")
                        pending.write_text(str(counter))
                        pending.replace(path)
                        counter += 1
                        time.sleep(0.025)
                finally:
                    os._exit(1)  # never enter F's exception/cleanup path
            self.add(role, self.observe(pid))
            self.wait(path.exists, f"{role} heartbeat missing", max(0, end - time.monotonic()))
            self.sentinels[role] = path
            before = self.sentinel_snapshot((role,))
            self.wait(lambda: self.sentinel_snapshot((role,), record=False)[role] > before[role],
                      f"{role} readiness heartbeat did not advance", max(0, end - time.monotonic()))
            self.sentinel_progress(before, self.sentinel_snapshot((role,)))
        except BaseException as exc:
            self.safety_violation("sentinel creation/readiness failed", role=role,
                                  error=f"{type(exc).__name__}: {exc}")

    def sentinel_snapshot(self, roles=("S", "J"), *, record=True):
        result = {}
        observations = {}
        for role in roles:
            try:
                identity = self.ledger[role]
                current = self.observe(identity["pid"])
                require(same(identity, current) and current["state"] not in ("Z", "X"),
                        f"sentinel {role} identity/liveness lost")
                with RAW_OPEN(self.sentinels[role], encoding="ascii") as stream:
                    value = stream.read(65)
                if role == "S" and self.heartbeat_fault_armed:
                    # One explicit S input, after genuine permission cleanup_error.
                    # Delegate the actual read; only this verifier observation is malformed.
                    self.heartbeat_fault_armed = False
                    self.heartbeat_fault_applied = True
                    self.event("S_heartbeat_input", actual_read=value, selected_value="malformed-S")
                    value = "malformed-S"
                require(0 < len(value) <= 64 and value.isascii() and value.isdecimal(),
                        f"invalid sentinel {role} heartbeat", observed=value,
                        expected="1..64 ASCII decimal characters")
                result[role] = int(value)
                observations[role] = {"identity": dict(identity), "observed": current,
                                      "heartbeat": result[role], "t": time.monotonic() - self.started}
            except BaseException as exc:
                self.safety_violation("sentinel observation unavailable or unsafe", role=role,
                                      error=f"{type(exc).__name__}: {exc}")
        if record:
            self.event("sentinel_observation", observations=observations)
        return result

    def sentinel_progress(self, before, after):
        if not all(role in after and after[role] > value for role, value in before.items()):
            self.safety_violation("sentinel heartbeat did not advance", before=before, after=after)

    def cleanup(self, roles, direct=(), deadline=None, workers=()):
        """Separate fixture rescue. Evidence is captured BEFORE calling this."""
        deadline = min(time.monotonic() + 10, self.final_deadline - 1) if deadline is None else deadline
        rescued = []
        for role in reversed(tuple(roles)):
            identity = self.ledger.get(role)
            if identity and self.is_live(identity):
                action = {"role": role, "identity": dict(identity), "boot": self.boot,
                          "decision_t": time.monotonic() - self.started, "signal": int(signal.SIGKILL)}
                self.event("fixture_rescue_signal_attempt", **action)
                try:
                    RAW_KILL(identity["pid"], signal.SIGKILL)
                except BaseException as exc:
                    self.event("fixture_rescue_signal_result", **action, outcome=f"{type(exc).__name__}: {exc}")
                    raise
                self.event("fixture_rescue_signal_result", **action, outcome="sent")
                rescued.append(role)
        # Rescue may unblock communication, but cannot take its direct wait.
        # All production owners must finish before F consumes any child status.
        for worker in workers:
            worker.join(timeout=max(0.0, deadline - time.monotonic()))
        require(all(not worker.is_alive() for worker in workers), "unsafe worker remains before fixture waits")
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
            require(not self.is_live(identity), f"unsafe residual: {role}")
            # Nonlive alone is insufficient: any surviving matching zombie
            # means F did not establish complete own-child reaping.
            require(not same(identity, self.observe(identity["pid"])), f"unreaped fixture: {role}")
            self.ledger.pop(role)
        self.event("fixture_rescue", roles=rescued)
        self.send()

    def close(self):
        self.case_deadline = min(time.monotonic() + 10, self.final_deadline - 1)
        failures = []
        try:
            self.cleanup([role for role in self.ledger if role not in ("F", "W")], workers=self.threads)
            for worker in self.threads:
                worker.join(timeout=max(0.0, self.case_deadline - time.monotonic()))
            require(all(not thread.is_alive() for thread in self.threads), "fixture thread remains")
        except BaseException as exc:
            failures.append(str(exc))
        finally:
            if self.sock is not None:
                try:
                    self.send("close")
                except BaseException as exc:
                    failures.append(str(exc))
                    identity = self.ledger.get("W")
                    if identity and self.is_live(identity):
                        RAW_KILL(identity["pid"], signal.SIGKILL)
                finally:
                    status = specific_wait(self.watchdog_pid, min(self.final_deadline, time.monotonic() + 1))
                    require(status is not None, "W wait missing")
                    require(not same(self.ledger["W"], self.observe(self.watchdog_pid)), "W remains")
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
        self.read_entry = None
        self.arm_read = False
        self.fault = None
        self.fault_hits = 0
        self.rows = 0
        self.scan_attempts = []
        self.read_operations = {}
        self.signal_attempts = []
        self.errors = []
        self.case_dirs = {}
        self.active_samplers = 0

    def probe(self):
        # Delegate the REAL operational probe, accounting its direct helper.
        original_popen = subprocess.Popen
        helpers = []
        helper_facts = []
        child_facts = {}
        report_observed = False
        stage = "backend_probe"

        def launched(*args, **kwargs):
            nonlocal stage
            stage = "helper_launch"
            process = original_popen(*args, **kwargs)
            helpers.append(process)
            fact = {"pid": process.pid}
            helper_facts.append(fact)
            stage = "helper_accounting"
            fact["launch_identity"] = stat(process.pid)
            self.v.add("probe", fact["launch_identity"])
            stage = "backend_probe"
            return process

        subprocess.Popen = launched
        try:
            try:
                report = self.backend.probe()
            finally:
                subprocess.Popen = original_popen
            report_observed = True
            # Retain the ORIGINAL before any observer postcondition can raise;
            # HostSupervisor may separately degrade such an exception.
            stage = "backend_report"
            self.v.event("probe_report", stage=stage, report=dataclasses.asdict(report))
            for process, fact in zip(helpers, helper_facts):
                stage = "helper_reaped"
                fact["returncode"] = process.returncode
                require(fact["returncode"] is not None, "probe helper was not reaped by backend")
                stage = "helper_absent"
                fact["identity"] = stat(process.pid)
                require(fact["identity"] is None, "probe helper remains")
            stage = "ledger_release"
            self.v.ledger.pop("probe", None)
            stage = "watchdog_send"
            self.v.send()
            stage = "children_read"
            with RAW_OPEN(f"/proc/{os.getpid()}/task/{os.getpid()}/children") as stream:
                children = [int(value) for value in stream.read(4096).split()]
            known = {row["pid"] for row in self.v.ledger.values()}
            unexpected = [pid for pid in children if pid not in known]
            child_facts.update(children=children, known=sorted(known), unexpected=unexpected,
                               observed_unexpected=[])
            for pid in unexpected:
                stage = "unexpected_child_accounting"
                current = stat(pid)
                child_facts["observed_unexpected"].append({"pid": pid, "identity": current})
                if current is not None:
                    self.v.add(f"probe-own-child-{pid}", current)
            stage = "children_accounted"
            require(not unexpected, "probe left an unaccounted own child; venue refuses")
        except BaseException as exc:
            self.v.event("probe_exception", stage=stage, backend_report_observed=report_observed,
                         exception_type=type(exc).__name__, message=str(exc),
                         helpers=helper_facts, children=child_facts)
            raise
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
        self.active_samplers += 1
        try:
            with self.marked("sample", owner):
                sample = self.backend.sample(running)
        finally:
            self.active_samplers -= 1
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
        self.v.event("finish_return", owner=owner, receipt=dataclasses.asdict(receipt),
                     receipt_identity=id(receipt))
        if self.case in F1_CASES:
            self.final_receipt = receipt
        return receipt

    def history_has(self, pid, start=None):
        running = self.running.get("A")
        if running is None:
            return False
        session = self.backend._sessions.get(running.token)
        members = session[-1] if isinstance(session, tuple) else getattr(session, "members", {})
        return any(observation.pid == pid and (start is None or observation.start_identity == start)
                   for observation in members.values())

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
                    observer.read_entry = {"t": time.monotonic(), "token": observer.running["A"].token}
                    observer.read_entered.set()
                    observer.v.event("sample_read_entered", token=observer.read_entry["token"],
                                     read_entry_t=observer.read_entry["t"])
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

        def guard(condition, message, **target):
            if not condition:
                observer.v.safety_violation(message, **target)

        def signal_identity(pid, sig):
            try:
                return stat(pid)
            except BaseException as exc:
                observer.v.safety_violation("signal identity observation unavailable", pid=pid,
                                            signal=int(sig), error=f"{type(exc).__name__}: {exc}")

        def killpg(pgid, sig):
            marker = getattr(TLS, "backend", None)
            guard(marker and marker[0] is observer, "unattributed group signal", pgid=pgid, signal=int(sig))
            owner = marker[2]
            observer.signal_attempts.append({"owner": owner, "pgid": pgid, "signal": int(sig),
                                             "t": time.monotonic() - observer.v.started})
            running = observer.running.get(owner)
            if (observer.case == "C3-reused-identity" and owner == "A" and marker[1] == "finish"
                    and running is not None and pgid == running.root_pid):
                observer.v.event("synthetic_replacement_signal_intercepted", pgid=pgid, signal=int(sig))
                return  # explicit synthetic decision classification, NEVER RAW
            guard(running is not None and pgid == running.root_pid,
                  "foreign numeric group target", pgid=pgid, signal=int(sig))
            for role in ("S", "J", "F", "W", "B0" if owner == "A" else "A0"):
                identity_row = observer.v.ledger.get(role)
                if identity_row:
                    current = signal_identity(identity_row["pid"], sig)
                    guard(not same(identity_row, current) or current["pgid"] != pgid,
                          f"collateral group target: {role}", pgid=pgid, signal=int(sig))
            owned_roles = ("A0", "A1", "A2") if owner == "A" else ("B0",)
            witnesses = [signal_identity(row["pid"], sig) for role, row in observer.v.ledger.items() if role in owned_roles]
            guard(any(current and current["pgid"] == pgid and current["state"] not in ("Z", "X")
                      and any(same(row, current) for role, row in observer.v.ledger.items() if role in owned_roles)
                      for current in witnesses), "group has no exact live fixture witness", pgid=pgid, signal=int(sig))
            if observer.case == "C5-teardown-error" and marker[1] == "finish":
                raise PermissionError("fixture delegated group-signal failure")
            return RAW_KILLPG(pgid, sig)

        def kill(pid, sig):
            marker = getattr(TLS, "backend", None)
            guard(marker and marker[0] is observer, "unattributed direct signal", pid=pid, signal=int(sig))
            role = "B0" if marker[2] == "B" else "A0"
            row = observer.v.ledger.get(role)
            guard(row is not None and row["pid"] == pid and same(row, signal_identity(pid, sig)),
                  "foreign direct signal target", pid=pid, signal=int(sig))
            return RAW_KILL(pid, sig)

        old_cap = getattr(census_module, "_CHECKED_ROW_LIMIT", None)
        reader_class.read_all, reader_class.read_process, reader_class.start_identity = read, process, identity
        builtins.open, os.scandir, os.getpgid = opened, scandir, getpgid
        os.killpg, os.kill = killpg, kill
        try:
            yield
        finally:
            if all(not worker.is_alive() for worker in self.v.threads) and self.active_samplers == 0:
                reader_class.read_all, reader_class.read_process, reader_class.start_identity = original_read, original_process, original_identity
                builtins.open, os.scandir, os.getpgid = RAW_OPEN, RAW_SCANDIR, RAW_GETPGID
                os.killpg, os.kill = RAW_KILLPG, RAW_KILL
            else:
                # Unknown worker settlement: keep interception until process
                # exit; close uses only separately captured fixture RAW calls.
                self.v.stopped = True
                self.v.report["safety_interception_retained"] = True
                self.v.report["worker_settlement_unknown"] = True
            if old_cap is not None:
                census_module._CHECKED_ROW_LIMIT = old_cap


def run_case(v, case, api, fault=None):
    from runtime.orchestrator.executors import CustomAdapterExecutor, ExecutorResult, _run_command
    from runtime.orchestrator.host_supervisor import LaunchResult, TerminalReason
    v.begin_case(case, fault)
    backend = api.backend()
    observer = BackendObserver(v, backend, case)
    publications, terminals, results, errors = [], [], {}, []
    f1_case = case in F1_CASES
    body_held, body_release = (threading.Event(), threading.Event()) if f1_case else (None, None)
    publisher_held, publisher_release = (threading.Event(), threading.Event()) if f1_case else (None, None)
    publication_error = RuntimeError("F1 contained publication sentinel") if f1_case else None
    f1 = None

    def publish(receipt):
        publications.append(receipt)
        v.event("publish", token=receipt.invocation_kind, receipt_identity=id(receipt))
        if f1_case:
            v.event("F1_publisher_held", thread=threading.get_ident(), receipt_identity=id(receipt))
            publisher_held.set()
            require(publisher_release.wait(max(0, v.case_deadline - time.monotonic())),
                    "F1 held publisher exceeded action deadline")
            if case == "C2-cancel-publish-error":
                raise publication_error

    # Real operational probe occurs before providers/overlap, never bypassed.
    supervisor = api.factory(backend=observer, publisher=publish)
    if f1_case:
        observer.final_receipt = None
        f1 = FinalizationObserver(v, observer, supervisor, publish)
    probe_report = supervisor.probe()
    v.event("probe_report", stage="supervisor_return", report=dataclasses.asdict(probe_report))
    if not probe_report.healthy:
        v.event("probe_refusal", stage="healthy_gate", provider_launched=False,
                final_interval="UNREACHED")
    require(probe_report.healthy, "process-group backend probe failed")
    original_release = supervisor._admission._release

    def released(lease):
        v.event("lease_release", logical_id=lease.ctx._logical_id)
        original_release(lease)
        if f1_case:
            v.event("release_committed", logical_id=lease.ctx._logical_id,
                    active=supervisor.active_count(), released=supervisor._admission.released_total())

    supervisor._admission._release = released
    original_finish = supervisor._finish_and_reconcile

    def finish_protocol(ctx):
        v.event("finish_protocol_enter", logical_id=ctx._logical_id)
        return original_finish(ctx)

    supervisor._finish_and_reconcile = finish_protocol
    original_start_sampler = supervisor._start_sampler

    def observed_start_sampler(*args, **kwargs):
        sampler = original_start_sampler(*args, **kwargs)
        if sampler is not None:
            # Retain and specifically join the REAL factory sampler thread;
            # an idle sampler is still a worker even outside backend.sample.
            v.threads.append(sampler._thread)
        return sampler

    supervisor._start_sampler = observed_start_sampler
    before_sentinels = None
    direct = []
    roles = []
    start_index = len(v.events)
    case_dirs = {}
    late_sample_count = None
    cleanup_deadline = min(v.case_deadline + 10, v.final_deadline - 1)
    # Existing factory cadence remains 1s. Give real readiness/history/root
    # exit 3s, THEN start the real 2s communicate timeout. Its existing 5s
    # drain + 5s TERM/2s KILL + up to 2s sampler stop fit the 20s action.
    timeout_setup_deadline = min(time.monotonic() + 3, v.case_deadline - 16)
    communicate_ready = threading.Event()

    def start(owner="A", mode=None):
        mode = mode or ("C2-cancel" if f1_case else case)
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
            timeout = 2 if case == "C2-timeout-pipe" else 12
            if case == "C2-timeout-pipe":
                require(communicate_ready.wait(max(0.0, timeout_setup_deadline - time.monotonic())),
                        "pipe timeout setup did not establish sampled children/root exit in 3s")
                require(not v.stopped, "unsafe fixture before communicate")
                v.event("pipe_communicate_start", owner=owner, timeout=timeout)
            with observer.marked("communicate", owner):
                if custom:
                    result = custom.run(directory, "synthetic fixture", session_id="synthetic-session-" + owner,
                                        timeout_seconds=timeout, running=running)
                else:
                    result = _run_command([str(executable)], directory, "synthetic-session-" + owner,
                                          timeout, input_text="synthetic fixture", provider="lifecycle-fixture",
                                          running=running, throttle_backoff_seconds=())
            v.event("communicate_result", owner=owner, success=result.success,
                    failure_category=result.failure_category, error=result.error)
            if f1_case:
                v.event("F1_body_held", owner=owner, thread=threading.get_ident())
                body_held.set()
                require(body_release.wait(max(0, v.case_deadline - time.monotonic())),
                        "F1 held launch-body return exceeded action deadline")
            return api.result(success=result.success, duration_seconds=result.duration_seconds,
                              returncode=result.returncode, error=result.error,
                              timed_out=result.failure_category == "provider_timeout", payload=result)

        def target():
            try:
                def terminal(outcome):
                    terminals.append(owner)
                    fields = ({"receipt_identity": id(outcome.receipt) if outcome else None,
                               "receipt": dataclasses.asdict(outcome.receipt) if outcome and outcome.receipt else None,
                               "terminal_reason": outcome.terminal_reason.value if outcome else None}
                              if f1_case else {})
                    v.event("terminal_hook", owner=owner, **fields)

                with f1.worker("run") if f1 else nullcontext():
                    results[owner] = supervisor.run(request, launch_spec=spec, launch_body=body,
                        pre_launch_validator=(lambda: require(custom.verify_launch_ready() is None,
                                                             "custom prelaunch readiness")) if custom else None,
                        on_terminal=terminal, timeout=3, allow_retries=False)
                    if f1_case:
                        v.event("run_return", owner=owner, receipt_identity=id(results[owner].receipt),
                                terminal_reason=results[owner].terminal_reason.value)
            except BaseException as exc:
                errors.append(f"{type(exc).__name__}: {exc}")

        thread = threading.Thread(target=target, name="lifecycle-run-" + owner)
        v.threads.append(thread)
        thread.start()
        return request, thread

    def adopt_role(role, owner="A"):
        directory = case_dirs[owner]
        ready = directory / (role + ".ready")
        seconds = max(0, timeout_setup_deadline - time.monotonic()) if case == "C2-timeout-pipe" else None
        v.wait(ready.exists, f"{role} readiness missing", seconds)
        v.add(role, json.loads(ready.read_text()))
        roles.append(role)
        (directory / (role + ".ack")).touch()

    record = {**v.scope, "status": "MISSING", "assertions": []}
    retained_identities = {}
    f1_mismatches = []

    def f1_events(target, phase, worker=None):
        return [e for e in f1.snapshot()["events"] if e["target"] == target
                and e["phase"] == phase and (worker is None or e["worker"] == worker)]

    def f1_overlap():
        contexts = supervisor._admission.ownerships_snapshot()
        require(len(contexts) == 1 and contexts[0].running() is observer.running["A"],
                "F1 lacks its actual bound attempt")
        f1.bind(contexts[0])

        def cancel():
            try:
                with f1.worker("control"):
                    request.cancellation.cancel()
            except BaseException as exc:
                errors.append(f"{type(exc).__name__}: {exc}")

        control = threading.Thread(target=cancel, name="lifecycle-cancel")
        v.threads.append(control)
        control.start()
        v.wait(lambda: body_held.is_set() and publisher_held.is_set(),
               "F1 body-return/publisher held acknowledgements missing")
        observed = f1.snapshot()
        record["F1_acknowledgements"] = observed
        require(not observed["errors"] and len(observed["workers"]) == 2
                and all(w["owned"] for w in observed["workers"]), "F1 trace ownership unavailable")
        require(len(f1_events("account", "call", "control")) == 1
                and len(f1_events("account", "return", "control")) == 1
                and len(f1_events("publisher", "call", "control")) == 1
                and not f1_events("publisher", "return") and not f1_events("publisher", "unwind")
                and observer.final_receipt is not None
                and publications == [observer.final_receipt]
                and not body_release.is_set() and not publisher_release.is_set(),
                "F1 held publication lacks real account/receipt/control attribution")
        v.event("F1_body_release_input")
        body_release.set()  # Release only the real launch-body return.
        v.wait(lambda: bool(f1_events("finalize", "return", "run")
                            or f1_events("wait", "call", "run") or f1.snapshot()["errors"]),
               "F1 competing finalizer opportunity missing")
        require(not f1.snapshot()["errors"], "F1 trace refused overlap evidence")
        premature = bool(f1_events("finalize", "return", "run"))
        if premature:
            v.wait(lambda: not thread.is_alive(), "F1 premature caller did not return its real outcome")
            thread.join()
        require(not publisher_release.is_set() and not f1_events("publisher", "return")
                and not f1_events("publisher", "unwind"), "F1 publisher already finished at overlap cut")
        held = {"terminals": len(terminals), "released": supervisor._admission.released_total(),
                "outcome_present": "A" in results, "active": supervisor.active_count()}
        expected = {"terminals": 0, "released": 0, "outcome_present": False, "active": 1}
        record["F1_held"] = {"opportunity": "premature-return" if premature else "same-condition-wait",
                             "observed": held, "expected": expected, "trace": f1.snapshot(),
                             "publisher_release_input": publisher_release.is_set(),
                             "body_held": body_held.is_set(), "publisher_held": publisher_held.is_set(),
                             "receipt": dataclasses.asdict(observer.final_receipt)}
        freeze_backend("F1_held_snapshot")
        if held != expected:
            mismatch = ContractMismatch("premature terminal/release with unfinished publication", held, expected, True)
            f1_mismatches.append(mismatch)
            record["F1_saved_mismatch"] = {"assertion": mismatch.assertion,
                                           "observed": held, "expected": expected}
        v.event("F1_publisher_release_input")
        publisher_release.set()  # Normal dependency input, never completion proof.
        v.wait(lambda: all(not w.is_alive() for w in v.threads) and observer.active_samplers == 0,
               "F1 real run/control/sampler did not settle after publication release")
        control.join()
        thread.join()

    def f1_production_settlement():
        proof = {"complete": False, "workers": {w.name: w.is_alive() for w in v.threads},
                 "active_samplers": observer.active_samplers, "errors": list(errors),
                 "trace": f1.snapshot(), "active": supervisor.active_count(),
                 "released": supervisor._admission.released_total()}
        record["F1_production_settlement"] = proof
        require(not proof["errors"] and not any(proof["workers"].values())
                and proof["active_samplers"] == 0 and proof["active"] == 0 and proof["released"] == 1,
                "F1 lacks normal production settlement before rescue", observed=proof,
                expected="real run/control/sampler returned; one released lease; no errors")
        trace = proof["trace"]
        require(not trace["errors"] and not trace["active_frames"] and len(trace["workers"]) == 2
                and {w["role"] for w in trace["workers"]} == {"run", "control"}
                and all(w["owned"] and w["restored"] for w in trace["workers"]),
                "F1 observer ownership/restoration/event completeness refused", observed=trace,
                expected="two owned/restored worker traces, no frames/errors")
        evidence = v.events[start_index:]
        indices = {kind: [i for i, e in enumerate(evidence) if e["kind"] == kind]
                   for kind in ("finish_enter", "finish_return", "publish", "terminal_hook",
                                "release_committed", "run_return")}
        require(all(len(items) == 1 for items in indices.values()), "F1 completion counts not exactly once",
                observed=indices, expected="one finish/account/publication attempt/terminal/release/outcome")
        receipt = results["A"].receipt
        identity = id(receipt)
        require(receipt is observer.final_receipt and publications == [receipt]
                and all(evidence[indices[kind][0]]["receipt_identity"] == identity
                        for kind in ("finish_return", "publish", "terminal_hook", "run_return"))
                and results["A"].terminal_reason.value == "cancelled"
                and evidence[indices["terminal_hook"][0]]["terminal_reason"] == "cancelled",
                "F1 actual receipt/terminal-hook argument/winner mismatch")
        for target in ("account", "publisher"):
            require(len(f1_events(target, "call", "control")) == 1,
                    "F1 missing/duplicate real control-owned callback attempt")
        require(len(f1_events("account", "return", "control")) == 1
                and not f1_events("account", "exception") and not f1_events("account", "unwind"),
                "F1 actual account did not return exactly once")
        error_case = case == "C2-cancel-publish-error"
        completed = f1_events("publisher", "unwind" if error_case else "return", "control")
        exceptions = f1_events("publisher", "exception", "control")
        require(len(completed) == 1
                and len(f1_events("publisher", "return")) == (0 if error_case else 1)
                and len(f1_events("publisher", "unwind")) == (1 if error_case else 0)
                and len(exceptions) == (1 if error_case else 0)
                and (not error_case or exceptions[0]["exception_identity"] == id(publication_error))
                and len(f1_events("finalize", "return", "control")) == 1
                and len(f1_events("finalize", "return", "run")) == 1
                and not f1_events("finalize", "unwind"),
                "F1 publisher success/error containment or caller liveness incomplete")
        finalizer_exceptions = f1_events("finalize", "exception", "control")
        require(len(finalizer_exceptions) == (1 if error_case else 0)
                and (not error_case or finalizer_exceptions[0]["exception_identity"] == id(publication_error)),
                "F1 real sentinel was not contained by the actual owner finalizer")
        if record["F1_held"]["opportunity"] == "same-condition-wait":
            require(len(f1_events("wait", "call", "run")) >= 1
                    and len(f1_events("wait", "call", "run")) == len(f1_events("wait", "return", "run"))
                    and not f1_events("wait", "exception") and not f1_events("wait", "unwind")
                    and len(f1_events("notify", "call", "control")) == 1
                    and len(f1_events("notify", "return", "control")) == 1,
                    "F1 same-condition wait/notify event completeness missing")
        # Actual finish/direct wait, account return and callback entry must be
        # ordered even for a RED. Premature external completion is saved below,
        # never made into a fake normal-settlement prerequisite.
        account_return = next(i for i, e in enumerate(evidence) if e["kind"] == "F1_trace"
                              and e["target"] == "account" and e["phase"] == "return")
        publisher_call = next(i for i, e in enumerate(evidence) if e["kind"] == "F1_trace"
                             and e["target"] == "publisher" and e["phase"] == "call")
        require(indices["finish_return"][0] < account_return < publisher_call,
                "F1 finish/account/publication entry order missing")
        proof.update(complete=True, receipt_identity=identity, receipt=dataclasses.asdict(receipt),
                     publication_attempts=len(f1_events("publisher", "call")),
                     publication_successes=len(f1_events("publisher", "return")),
                     sentinel_exceptions=len(exceptions), event_indices=indices,
                     completed_t=time.monotonic())
        # Compare actual event positions (timestamps can round equal). Do not
        # impose owner-function return before the notified waiter's return.
        publication_end = next(
            i for i, e in enumerate(evidence) if e["kind"] == "F1_trace"
            and e["target"] == "publisher" and e["phase"] in ("return", "unwind"))
        competitor_end = next(i for i, e in enumerate(evidence) if e["kind"] == "F1_trace"
                              and e["target"] == "finalize" and e["worker"] == "run" and e["phase"] == "return")
        ordered = publication_end < competitor_end < indices["terminal_hook"][0] < indices["release_committed"][0] < indices["run_return"][0]
        handling_settled = [i for i, e in enumerate(evidence) if e["kind"] == "F1_trace"
                            and e["target"] == "notify" and e["worker"] == "control" and e["phase"] == "return"]
        if record["F1_held"]["opportunity"] == "same-condition-wait":
            ordered = ordered and len(handling_settled) == 1 and publication_end < handling_settled[0] < competitor_end
        proof["external_order"] = {"publication_end": publication_end, "competitor_end": competitor_end,
                                    "owner_handling_settled": handling_settled, **indices, "ordered": ordered}
        if not ordered and not f1_mismatches:
            f1_mismatches.append(ContractMismatch("F1 publication settlement before external completion",
                                                 proof["external_order"], "publication end < competitor < terminal < committed release < run return", True))

    def failure(exc):
        details = {"type": type(exc).__name__, "message": str(exc),
                   "trace": traceback.format_exc(limit=12)}
        if isinstance(exc, ContractMismatch):
            details.update(assertion=exc.assertion, observed=exc.observed,
                           expected=exc.expected, explicit_values=exc.explicit_values)
            if exc.assertion == "matching watchdog rescue before last acceptance observation":
                cutoff = record.get("finalizer", {}).get("cutoff")
                details.update(observed={"cutoff": cutoff, "rescues": [r for r in v.watchdog_rescues
                               if r["scope"] == v.scope]},
                               expected="no matching W rescue decision at or before the frozen cutoff",
                               explicit_values=True)
        cause = exc.__context__
        if isinstance(cause, ContractMismatch):
            details["cause"] = {"type": type(cause).__name__, "assertion": cause.assertion,
                                "observed": cause.observed, "expected": cause.expected,
                                "explicit_values": cause.explicit_values}
        return details

    def freeze_backend(label):
        # Always called before rescue, including setup/assertion failure. An
        # absent outcome is explicit, alongside actual retained terminal events.
        snapshots = {}
        for owner, outcome in tuple(results.items()):
            snapshots[owner] = {"terminal_reason": outcome.terminal_reason.value,
                                "cleanup_error": outcome.cleanup_error,
                                "receipt": dataclasses.asdict(outcome.receipt) if outcome.receipt else None}
        identities = {}
        for role in tuple(v.ledger.keys() | retained_identities.keys()):
            identity = v.ledger.get(role, retained_identities.get(role))
            if identity is not None:
                retained_identities[role] = dict(identity)
                if v.stopped:
                    identities[role] = {"identity": dict(identity), "observed": "UNAVAILABLE: fixture stopped"}
                    continue
                try:
                    identities[role] = {"identity": dict(identity), "observed": v.observe(identity["pid"])}
                except BaseException as exc:
                    # observe already latched fatal unknown. Keep this failure
                    # snapshot and continue bounded rescue/worker settlement.
                    identities[role] = {"identity": dict(identity), "observed": "UNKNOWN",
                                        "error": f"{type(exc).__name__}: {exc}"}
        snapshot = {"outcomes": snapshots,
                    "outcome_A": "present" if "A" in snapshots else "ABSENT/UNSETTLED",
                    "errors": list(errors), "workers": {w.name: w.is_alive() for w in v.threads},
                    "identities": identities,
                    "signal_attempts": list(observer.signal_attempts),
                    "active_samplers": observer.active_samplers,
                    "stopped": v.stopped,
                    "event_range": [start_index, len(v.events)],
                    "safety_violations": list(v.safety_violations)}
        if f1_case:
            snapshot["F1"] = {"held": record.get("F1_held"), "trace": f1.snapshot(),
                              "normal_settlement": record.get("F1_production_settlement")}
        # LAST observation: materialize every independent acceptance fact above
        # before reading this cutoff. The event below only labels frozen data.
        snapshot["t"] = time.monotonic() - v.started
        record[label] = snapshot
        v.event(label, outcome_A=snapshot["outcome_A"])
        return snapshot["t"]

    def c_production_settlement():
        # Observe only existing returned results/events. No fixture poll/wait,
        # rescue, or target clean/quiescent assertion can supply this proof.
        proof = {"complete": False, "errors": list(errors),
                 "workers": {w.name: w.is_alive() for w in v.threads},
                 "active_samplers": observer.active_samplers,
                 "returned_owners": list(results), "terminals": list(terminals),
                 "active_leases": supervisor.active_count(),
                 "released_total": supervisor._admission.released_total(),
                 "owners": {}, "events": [dict(e) for e in v.events[start_index:]]}
        record["C_production_settlement"] = proof
        try:
            require(not proof["errors"], "C normal settlement has worker errors",
                    observed=proof["errors"], expected=[])
            require(set(proof["returned_owners"]) == {"A", "B"}, "C missing actual A/B outcome",
                    observed=proof["returned_owners"], expected=["A", "B"])
            require(not any(proof["workers"].values()) and proof["active_samplers"] == 0,
                    "C production workers/samplers unsettled",
                    observed={"workers": proof["workers"], "active_samplers": proof["active_samplers"]},
                    expected="all actual workers and samplers settled before rescue")
            require(proof["active_leases"] == 0 and proof["released_total"] == 2,
                    "C production leases not independently released once per owner",
                    observed={"active": proof["active_leases"], "released": proof["released_total"]},
                    expected={"active": 0, "released": 2})
            require(len(publications) == 2 and sorted(proof["terminals"]) == ["A", "B"],
                    "C missing/duplicate production publications or terminal hooks")
            evidence = proof["events"]
            require(sum(e["kind"] == "publish" for e in evidence) == 2,
                    "C missing/duplicate publication events")
            for owner in ("A", "B"):
                outcome, running = results[owner], observer.running[owner]
                receipt = outcome.receipt
                owner_proof = {"logical_id": outcome.request.logical_id, "token": running.token,
                               "pid": running.root_pid, "terminal_reason": outcome.terminal_reason.value,
                               "cleanup_error": outcome.cleanup_error,
                               "receipt": dataclasses.asdict(receipt) if receipt else None,
                               "receipt_identity": id(receipt),
                               "root_observation": v.observe(running.root_pid)}
                proof["owners"][owner] = owner_proof
                require(outcome.request.logical_id == "synthetic-lifecycle-" + owner,
                        "C wrong-owner returned outcome", observed=owner_proof,
                        expected="actual owner's returned outcome")
                indices = {kind: [i for i, e in enumerate(evidence)
                                  if e["kind"] == kind and e.get("owner") == owner]
                           for kind in ("finish_enter", "finish_return", "finish_error",
                                        "backend_direct_wait", "terminal_hook")}
                require(len(indices["finish_return"]) == 1, "C missing/duplicate actual finish return",
                        observed=indices, expected="one actual finish return for this owner")
                finished = evidence[indices["finish_return"][0]]
                # Match the actual finish to its publication, independently of
                # the returned outcome's target receipt values/immutability.
                owner_proof["finish_receipt_identity"] = finished["receipt_identity"]
                require(sum(id(r) == finished["receipt_identity"] for r in publications) == 1,
                        "C actual finish receipt not published exactly once")
                indices["publish"] = [i for i, e in enumerate(evidence) if e["kind"] == "publish"
                                      and e.get("receipt_identity") == finished["receipt_identity"]]
                indices["lease_release"] = [i for i, e in enumerate(evidence)
                                            if e["kind"] == "lease_release"
                                            and e["logical_id"] == outcome.request.logical_id]
                owner_proof["event_indices"] = indices
                require(all(len(indices[k]) == 1 for k in
                            ("finish_enter", "finish_return", "publish", "terminal_hook", "lease_release"))
                        and not indices["finish_error"], "C missing/duplicate production completion event",
                        observed=owner_proof, expected="one successful finish/publication/terminal/release per owner")
                enter, returned, published, hook, released = (indices[k][0] for k in
                    ("finish_enter", "finish_return", "publish", "terminal_hook", "lease_release"))
                waits = indices["backend_direct_wait"]
                require(enter < returned < published < hook < released and waits
                        and max(waits) < hook
                        and all(evidence[i]["pid"] == running.root_pid
                                and evidence[i]["operation"] in ("communicate", "finish") for i in waits)
                        and any(enter < i < returned and evidence[i]["operation"] == "finish" for i in waits),
                        "C production finish/direct wait/publication/terminal/release order",
                        observed=owner_proof,
                        expected="actual owner finish contains direct wait; return < publish < hook < release; all waits before hook, no F wait")
                require(evidence[enter]["token"] == running.token,
                        "C finish belongs to a different containment token")
                require(not same(v.ledger[owner + "0"], owner_proof["root_observation"]),
                        "C direct root unreaped before rescue", observed=owner_proof,
                        expected="matching root absent after actual production wait")
            require(not v.stopped and not v.safety_violations and time.monotonic() < v.case_deadline,
                    "C normal production completion unavailable within safe action interval")
            proof.update(complete=True, completed_t=time.monotonic())
            record["C_hold"]["production_A_B_settled_t"] = proof["completed_t"]
        except BaseException as exc:
            proof["failure"] = failure(exc)
            raise

    def acceptance_finalizer():
        # Observation only, inside the original action. Never poll/wait/reap,
        # release a held read, or spend cleanup time obtaining acceptance.
        finalizer_started = time.monotonic()
        require(not v.stopped, "acceptance finalizer unavailable after fatal stop")
        require(time.monotonic() < v.case_deadline, "acceptance finalizer action deadline")
        if case == "C4-owner-isolation":
            require(record.get("C_production_settlement", {}).get("complete") is True
                    and "C_normal_settlement_failure" not in record,
                    "C acceptance unavailable without proved normal A/B production settlement",
                    observed=record.get("C_production_settlement"),
                    expected="both returned outcomes and ordered production completion proof before rescue")
        if f1_case:
            require(record.get("F1_production_settlement", {}).get("complete") is True
                    and "F1_normal_settlement_failure" not in record,
                    "F1 acceptance unavailable without real normal production settlement",
                    observed=record.get("F1_production_settlement"),
                    expected="actual run/control/sampler and callback settlement before rescue")
        after = v.sentinel_snapshot()
        require(before_sentinels is not None, "acceptance lacks initial sentinel observations")
        v.sentinel_progress(before_sentinels, after)
        v.budgets()
        record["acceptance_sentinels"] = {"before": before_sentinels, "after": after}
        cutoff = freeze_backend("acceptance_evidence_frozen")
        frozen = record["acceptance_evidence_frozen"]
        require(not frozen["stopped"] and not frozen["safety_violations"]
                and time.monotonic() < v.case_deadline,
                "acceptance observations incomplete within action deadline")
        if case == "C4-owner-isolation":
            require(time.monotonic() - finalizer_started <= 1, "C final observations exceeded 1s planning cap")
        record["finalizer"] = {"observations_complete": True, "cutoff": cutoff,
                               "watchdog_accepted": False}
        check_started = time.monotonic()
        v.exchange_deadline = min(v.case_deadline, check_started + 1)
        try:
            v.check_watchdog(cutoff)  # unchanged ordered check, for success AND failure
            require(time.monotonic() < v.case_deadline, "acceptance W exchange exceeded action deadline")
            if case == "C4-owner-isolation":
                require(time.monotonic() - check_started <= 1, "C ordered W exchange exceeded 1s planning cap")
            record["finalizer"]["watchdog_accepted"] = True
        finally:
            v.exchange_deadline = None

    def watchdog_diagnostic():
        if v.stopped or time.monotonic() >= v.case_deadline:
            v.safety_violation("U unsafe/expired prerequisite")
        proof = {}
        for role, identity in tuple(v.ledger.items()):
            if role in ("F", "W", "S", "J"):
                continue
            current = v.observe(identity["pid"])
            if same(identity, current) and current["state"] not in ("Z", "X"):
                v.safety_violation("U refuses live rescue target", role=role, observed=current,
                                   expected="each nonsentinel target absent or nonlive")
            proof[role] = {"identity": dict(identity), "observed": current}
        if not proof:
            v.safety_violation("U has no independently observed rescue targets")
        record["U"] = {"prerequisites": proof, "deadline_input": time.monotonic()}
        v.exchange_deadline = v.case_deadline
        try:
            prior = len(v.watchdog_rescues)
            v.send(rescue_deadline=record["U"]["deadline_input"])
            # W decides after the first acknowledgement, before receiving the
            # next ledger. The second ordered acknowledgement retains its real decision.
            v.send()
            decisions = v.watchdog_rescues[prior:]
            require(len(decisions) == 1 and decisions[0]["scope"] == v.scope,
                    "U missing attributed W decision")
            actions = decisions[0]["actions"]
            require(len(actions) == len(proof) and {a["role"] for a in actions} == set(proof)
                    and all(a["identity"] == proof[a["role"]]["identity"]
                            and a["outcome"] == "identity-absent-or-nonlive; no signal"
                            and "action_t" not in a for a in actions),
                    "U decision unsafe/unknown or signaled a target")
            record["U"].update(decision=decisions[0], no_signal_decision=True)
        except BaseException:
            v.stopped = True
            raise
        finally:
            v.exchange_deadline = None

    with observer.dependencies(api.census):
        try:
            before_sentinels = v.sentinel_snapshot()
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
                    seconds = max(0, timeout_setup_deadline - time.monotonic()) if case == "C2-timeout-pipe" else None
                    v.wait(lambda: all(observer.history_has(v.ledger[role]["pid"], v.ledger[role]["start"])
                                       for role in ("A1", "A2")), "owned children never sampled", seconds)
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
                entry = observer.read_entry
                require(entry is not None and entry["token"] == observer.running["A"].token,
                        "C missing genuine read-entry timestamp/token")
                now = time.monotonic()
                barrier_end = entry["t"] + 12
                require(now < barrier_end and not observer.read_release.is_set(),
                        "C captured-read barrier already expired/released")
                r12, r_action = barrier_end - now, v.case_deadline - now
                h_max = max(0, min(8, r12 - 1, r_action - 14))
                record["C_hold"] = {"read_entry": entry, "admission_t": now,
                                    "R12": r12, "R_action": r_action,
                                    "action_limit_seconds": v.case_action_limit_seconds, "h_max": h_max,
                                    "minimum_opportunity": 5.5, "release_margin": 1,
                                    "reserve": {"A": 9, "B": 2, "final": 2, "slack": 1},
                                    "selected_sensitivity": api.selection == "C"}
                saved_mismatches = []
                record["C_mismatches"] = []
                if api.selection == "C" and h_max < 5.5:
                    record["missing_sensitivity"] = "conditional hold cannot fit: h_max < 5.5s"

                def save_condition(value, message, observed, expected):
                    if not value:
                        mismatch = ContractMismatch(message, observed, expected, True)
                        saved_mismatches.append(mismatch)
                        record["C_mismatches"].append({"assertion": message, "observed": observed,
                                                       "expected": expected})
                        v.event("C_saved_mismatch", assertion=message, observed=observed, expected=expected)

                try:
                    # B admission, readiness and control above consume BOTH clocks.
                    # Normal runs retain their immediate release; only explicit C
                    # requests the conditional sensitivity opportunity.
                    if api.selection == "C" and h_max >= 5.5:
                        hold_end = now + h_max
                        while True:
                            now = time.monotonic()
                            if (not control.is_alive() or not thread.is_alive()
                                    or now >= hold_end or v.case_deadline - now <= 14
                                    or barrier_end - now <= 1):
                                break
                            v.boundary()
                            time.sleep(min(.01, max(0, min(hold_end, v.case_deadline - 14,
                                                         barrier_end - 1) - time.monotonic())))
                    terminal_before = results.get("A")
                    frozen = (json.dumps(dataclasses.asdict(terminal_before.receipt), sort_keys=True)
                              if terminal_before and terminal_before.receipt else None)
                    held = {"outcome_present": terminal_before is not None, "publications": len(publications),
                            "terminals": list(terminals), "active": supervisor.active_count()}
                    save_condition(terminal_before is None and not publications and not terminals
                                   and held["active"] == 2,
                                   "premature final evidence/release with unsettled read", held,
                                   {"outcome_present": False, "publications": 0, "terminals": [], "active": 2})
                    record["C_hold"].update(held_observation=held, frozen_receipt=frozen,
                                            admitted=h_max >= 5.5,
                                            release_t=time.monotonic())
                    if terminal_before is None:
                        v.event("sample_finish_serialized", terminal_frozen=True,
                                published=bool(publications), active=held["active"])
                finally:
                    # Also release promptly on refusal. An unsafe/overrun path
                    # cannot credit later fixture rescue as production completion.
                    observer.read_release.set()
                v.wait(lambda: not control.is_alive() and not thread.is_alive(),
                       "A did not settle after read release",
                       max(0, record["C_hold"]["release_t"] + 9 - time.monotonic()))
                control.join()
                record["C_hold"]["A_settled_t"] = time.monotonic()
                save_condition(observer.running["A"].token not in backend._sessions, "retired A resurrected",
                               {"token": observer.running["A"].token,
                                "present": observer.running["A"].token in backend._sessions}, {"present": False})
                if frozen:
                    actual_frozen = (json.dumps(dataclasses.asdict(results["A"].receipt), sort_keys=True)
                                     if results.get("A") and results["A"].receipt else None)
                    save_condition(actual_frozen == frozen, "terminal A evidence changed after delayed read",
                                   actual_frozen, frozen)
                save_condition(b_context in supervisor._admission.ownerships_snapshot()
                               and b_context.running() == observer.running["B"] and "B" not in terminals,
                               "A changed/released B context", {"B_terminal": "B" in terminals,
                               "B_owned": b_context in supervisor._admission.ownerships_snapshot()},
                               {"B_terminal": False, "B_owned": True})
                save_condition(len(b_context.samples_snapshot()) >= len(b_samples), "B sample/account loss",
                               len(b_context.samples_snapshot()), {"minimum": len(b_samples)})
                save_condition(v.is_live(v.ledger["B0"]), "A killed B", v.observe(v.ledger["B0"]["pid"]),
                               {"identity": v.ledger["B0"], "live": True})
                b_end = time.monotonic() + 2
                (case_dirs["B"] / "exit").touch()
                v.wait(lambda: not b_thread.is_alive() and all(not w.is_alive() for w in v.threads)
                       and observer.active_samplers == 0, "B independent finish/sampler settlement missing",
                       max(0, b_end - time.monotonic()))
                b_thread.join()
                c_production_settlement()
                if "missing_sensitivity" in record:
                    require(False, record["missing_sensitivity"], observed=record["C_hold"],
                            expected="admitted >=5.5s opportunity; no deadline extension")
                if saved_mismatches:
                    raise saved_mismatches[0]
                if api.selection == "C":
                    record["missing_sensitivity"] = "no attributable wrong completion/value in finite held-read window"
                    require(False, record["missing_sensitivity"], observed=record["C_hold"],
                            expected="admitted >=5.5s opportunity and attributable C mismatch; no timeout-as-RED")
            elif f1_case:
                f1_overlap()
            else:
                if fault:
                    observer.fault = fault
                    if fault == "cap":
                        require(hasattr(api.census, "_CHECKED_ROW_LIMIT"), "checked private cap unavailable")
                        api.census._CHECKED_ROW_LIMIT = 8
                    else:
                        v.wait(lambda: observer.fault_hits > 0, "exact low-level PermissionError not reached")
                    require(v.is_live(v.ledger["A1"]), "fault oracle child not live")
                (case_dirs["A"] / "exit").touch()
                if case == "C2-timeout-pipe":
                    running = observer.running["A"]
                    root_exit = {}

                    def root_exited():
                        current = v.observe(running.root_pid)
                        if not same(v.ledger["A0"], current):
                            v.safety_violation("root identity unavailable before communicator", pid=running.root_pid,
                                               observed=current)
                        root_exit.update(identity=dict(v.ledger["A0"]), observed=current,
                                         t=time.monotonic() - v.started)
                        return current["state"] in ("Z", "X")

                    v.wait(root_exited, "root did not exit before pipe timeout",
                           max(0, timeout_setup_deadline - time.monotonic()))
                    # Exact OS oracle: BOTH sampled same-group descendants
                    # actually retain the Popen stdout pipe after root exit.
                    pipe = os.readlink(f"/proc/self/fd/{running.process.stdout.fileno()}")
                    holders = {}
                    sampled = {}
                    for role in ("A1", "A2"):
                        row = v.ledger[role]
                        current = v.observe(row["pid"])
                        require(same(row, current) and current["state"] not in ("Z", "X")
                                and current["pgid"] == running.root_pid, "pipe holder identity/group changed")
                        stdout = os.readlink(f"/proc/{row['pid']}/fd/1")
                        require(stdout == pipe, "descendant lacks actual stdout pipe")
                        require(observer.history_has(row["pid"], row["start"]), "pipe holder missing authentic factory history")
                        holders[role] = {"identity": dict(row), "observed": current, "stdout": stdout,
                                         "t": time.monotonic() - v.started}
                        sampled[role] = {"pid": row["pid"], "start": row["start"], "history_has": True}
                    require(time.monotonic() < timeout_setup_deadline, "pipe setup exceeded 3s allowance")
                    record["pipe_preconditions"] = {"root_exit_observation": root_exit,
                                                     "root_reaping": "NOT observed; production owner retains wait",
                                                     "factory_sampled": sampled,
                                                     "factory_sample_count": observer.samples.get("A", 0),
                                                     "holders": holders, "pipe": pipe}
                    v.event("root_exited_pipe_held", **record["pipe_preconditions"])
                    communicate_ready.set()
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
                           "known_live_before_rescue": {role: v.is_live(v.ledger[role]) for role in roles},
                           "factory_samples": observer.samples})
            # Freeze backend result BEFORE separate fixture rescue/reaping.
            freeze_backend("backend_evidence_frozen")
            require(not v.safety_violations, "fixture signal safety violation")
            require(sum(e["kind"] == "finish_enter" and e["owner"] == "A" for e in evidence) == 1,
                    "finish not exactly once")
            require(sum(e["kind"] == "terminal_hook" and e["owner"] == "A" for e in evidence) == 1,
                    "terminal hook not exactly once")
            require(supervisor.active_count() == 0 and supervisor._admission.released_total() == len(direct),
                    "lease not independently released exactly once")
            waits = [i for i,e in enumerate(evidence) if e["kind"] == "backend_direct_wait" and e["owner"] == "A"]
            hooks = [i for i,e in enumerate(evidence) if e["kind"] == "terminal_hook" and e["owner"] == "A"]
            releases = [i for i,e in enumerate(evidence) if e["kind"] == "lease_release" and e["logical_id"].endswith("-A")]
            require(waits and max(waits) < hooks[0] < releases[0], "direct wait/terminal/release order",
                    observed={"wait_indices": waits, "hook_indices": hooks, "release_indices": releases},
                    expected="actual production waits present, last wait < terminal hook < lease release")
            require(all(evidence[i]["operation"] in ("communicate", "finish") for i in waits),
                    "fixture consumed direct wait before backend evidence")
            root_observation = v.observe(v.ledger["A0"]["pid"])
            require(not same(v.ledger["A0"], root_observation), "backend direct root unreaped",
                    observed={"identity": v.ledger["A0"], "observed": root_observation},
                    expected="matching direct root absent after production wait")
            record["assertions"].append("one finish/direct wait/terminal hook/lease release in order")
            if case == "C1-late-in-group":
                require(observer.samples.get("A") == late_sample_count, "late child was not after the last periodic sample")
                remaining = [role for role in roles if v.is_live(v.ledger[role])]
                require((receipt is not None and not remaining and receipt.quiescent)
                        or (outcome.cleanup_error and not supervisor._residue.evaluate().admit)
                        or (receipt is not None and not receipt.quiescent),
                        "late child falsely clean without fresh authenticated group witness",
                        observed={"live_roles": remaining, "receipt": dataclasses.asdict(receipt) if receipt else None,
                                  "cleanup_error": outcome.cleanup_error, "gate_admit": supervisor._residue.evaluate().admit},
                        expected="no live child with quiescent receipt, or explicit uncertainty/nonquiescence and closed gate")
                require(observer.read_operations.get("finish", 0) > 0, "fresh finish observation missing",
                        observed=observer.read_operations.get("finish", 0), expected={"minimum": 1})
                record["assertions"].append("fresh terminal observation cleans late child or retains explicit uncertainty")
            elif fault or case in ("C3-reused-identity", "C5-teardown-error"):
                if case == "C3-reused-identity":
                    require(not observer.signal_attempts, "replacement numeric group received signal attempt",
                            observed=list(observer.signal_attempts), expected=[])
                require(outcome.cleanup_error or (receipt and not receipt.quiescent), "fault became clean",
                        observed={"cleanup_error": outcome.cleanup_error,
                                  "receipt": dataclasses.asdict(receipt) if receipt else None},
                        expected="retained cleanup_error or nonquiescent receipt")
                require(not supervisor._residue.evaluate().admit, "unknown cleanup did not close gate",
                        observed=supervisor._residue.evaluate().admit, expected=False)
                require(outcome.terminal_reason.value == "success", "cleanup replaced provider success reason")
                require(len(publications) == (1 if receipt else 0), "fabricated/duplicate publication")
                if fault == "permission":
                    require(observer.fault_hits > 0 and v.is_live(v.ledger["A1"]), "permission fault not proven")
                if fault == "cap":
                    require(observer.scan_attempts and all(a["rows"] == 9 for a in observer.scan_attempts)
                            and v.is_live(v.ledger["A1"]), "row9/private cap8 not exercised")
                record["assertions"].append("real fault/reuse refused clean outcome and closed gate")
            elif case == "C1-escaped-known":
                identity_row = v.ledger["A1"]
                require(observer.samples.get("A", 0) >= 2, "factory sampler missing",
                        observed=observer.samples.get("A", 0), expected={"minimum": 2})
                require(receipt is not None and not receipt.quiescent
                        and any(s.pid == identity_row["pid"] and s.start_identity == identity_row["start"]
                                for s in receipt.survivors) and v.is_live(identity_row), "known escaped identity forgotten",
                        observed={"identity": dict(identity_row), "observed": v.observe(identity_row["pid"]),
                                  "receipt": dataclasses.asdict(receipt) if receipt else None},
                        expected="actual live A1 PID/start retained in nonquiescent receipt")
                require(any(s.pid == identity_row["pid"] for s in supervisor._residue.census()), "escaped identity not charged")
                record["assertions"].append("factory sampling retains escaped live identity in receipt and residue")
            else:
                require(receipt is not None, "missing cleanup receipt")
                remaining = [role for role in roles if v.is_live(v.ledger[role])]
                require(not remaining or not receipt.quiescent, "live owned child falsely clean",
                        observed={"live_roles": remaining, "receipt": dataclasses.asdict(receipt)},
                        expected="no live owned child with quiescent receipt")
                if case != "C1-late-in-group":
                    require(not remaining and receipt.quiescent, "owned group cleanup incomplete",
                            observed={"live_roles": remaining, "receipt": dataclasses.asdict(receipt)},
                            expected={"live_roles": [], "quiescent": True})
                require(len(publications) == len(direct), "receipt not published once per invocation")
                if case == "C2-shutdown-bind":
                    require(outcome.terminal_reason.value == "shutdown" and not observer.started
                            and not observer.samples and not any(e["kind"] == "body_enter" for e in evidence),
                            "bind gate did not skip body/on_started/sampler")
                    require(any(e["kind"] == "backend_direct_wait" and e["operation"] == "finish" for e in evidence),
                            "backend wait missing from skipped-body cut")
                if f1_case:
                    require(outcome.terminal_reason.value == "cancelled", "cancel winner lost")
                    require(any(s["signal"] == signal.SIGKILL for s in observer.signal_attempts), "KILL escalation missing")
                if case == "C2-timeout-pipe":
                    require(outcome.terminal_reason is TerminalReason.TIMEOUT,
                            "real pipe-holder terminal timeout lost")
                    launch_result = outcome.payload
                    require(isinstance(launch_result, LaunchResult), "timeout outcome lacks LaunchResult",
                            observed=type(launch_result).__name__, expected="LaunchResult")
                    require(launch_result.timed_out is True and launch_result.success is False,
                            "timeout LaunchResult flags lost")
                    executor_result = launch_result.payload
                    require(isinstance(executor_result, ExecutorResult), "timeout LaunchResult lacks ExecutorResult")
                    require(executor_result.failure_category == "provider_timeout" and executor_result.success is False,
                            "real pipe-holder communicate timeout lost")
                    record["timeout_result"] = {
                        "timed_out": launch_result.timed_out, "launch_success": launch_result.success,
                        "failure_category": executor_result.failure_category, "executor_success": executor_result.success,
                    }
                if case == "C3-root-gone":
                    require(any(e["kind"] == "finish_enter" and e["root_returncode"] is not None for e in evidence),
                            "root was not already waited before finish")
                record["assertions"].append("real contained communication/group cleanup and case-specific final transition")
            if v.diagnostic == "S":
                require(fault == "permission" and outcome.cleanup_error is not None,
                        "S requires genuine first-input permission cleanup_error")
                record["S"] = {"cleanup_error": outcome.cleanup_error, "selected_input": "permission"}
                v.heartbeat_fault_armed = True
            if v.diagnostic == "U":
                watchdog_diagnostic()
            if f1_case:
                f1_production_settlement()
            acceptance_finalizer()
            if f1_mismatches:
                raise f1_mismatches[0]
            record["status"] = "PASS"
        except Exception as exc:
            record.update(status="FAIL", error=f"{type(exc).__name__}: {exc}", primary_failure=failure(exc))
            if case == "C4-owner-isolation" and not record.get("C_production_settlement", {}).get("complete"):
                # Keep the original exception AND every saved target mismatch.
                # Missing proof is fatal even if rescue later settles the venue.
                record["C_normal_settlement_failure"] = record.get("C_production_settlement", {}).get(
                    "failure", {"message": "normal A/B production completion not established before rescue",
                                "cause": record["primary_failure"]})
                v.stopped = True
            if f1_case and not record.get("F1_production_settlement", {}).get("complete"):
                record["F1_normal_settlement_failure"] = {
                    "message": "real F1 run/control/sampler/callback settlement not established before rescue",
                    "cause": record["primary_failure"]}
                v.stopped = True
            v.event("case_failure", error=record["error"])
        finally:
            # Keep dependencies, especially both signal interceptors, installed
            # throughout rescue and all backend/control/sampler settlement.
            if record["status"] != "PASS":
                if "acceptance_evidence_frozen" not in record and not v.stopped:
                    try:
                        acceptance_finalizer()
                    except BaseException as exc:
                        record["finalization_error"] = failure(exc)
                        v.stopped = True
                elif not record.get("finalizer", {}).get("watchdog_accepted"):
                    # U must fail the unchanged ordered cutoff assertion. It
                    # never retries that check or certifies a production RED.
                    if (v.diagnostic == "U" and record.get("U", {}).get("no_signal_decision")
                            and record.get("primary_failure", {}).get("assertion") ==
                            "matching watchdog rescue before last acceptance observation"
                            and not v.stopped and not v.safety_violations):
                        record["diagnostic_result"] = "U: expected attributed no-signal W refusal; FAIL"
                    else:
                        record.setdefault("finalization_error", {
                            "message": "acceptance finalizer incomplete/refused; no intended RED interval"})
                        v.stopped = True
                if v.diagnostic == "S" and record.get("S"):
                    record["S"].update(input_applied=v.heartbeat_fault_applied, stopped=v.stopped)
                    record["diagnostic_result"] = "S: fatal diagnostic; FAIL, no complete production RED interval"
            # Reuse the LAST frozen cutoff; never enlarge the accepted interval
            # with new observations after its ordered W check (including U).
            if "acceptance_evidence_frozen" in record:
                record["before_fixture_rescue"] = record["acceptance_evidence_frozen"]
                record["before_fixture_rescue_source"] = "acceptance_evidence_frozen; same cutoff"
            else:
                freeze_backend("before_fixture_rescue")
                record["before_fixture_rescue_source"] = "unaccepted failure snapshot; finalizer unavailable"
                if record["status"] != "PASS":
                    v.stopped = True
            observer.read_release.set()
            observer.launch_release.set()
            if f1_case:
                body_release.set()
                publisher_release.set()
            try:
                for owner, running in tuple(observer.running.items()):
                    if running.process not in direct:
                        direct.append(running.process)
                    role = "B0" if owner == "B" else "A0"
                    if role not in v.ledger and v.observe(running.root_pid) is not None:
                        v.add(role, v.observe(running.root_pid))
                        roles.append(role)
                v.cleanup(roles, direct, deadline=cleanup_deadline, workers=v.threads)
                for worker in v.threads:
                    worker.join(timeout=max(0.0, cleanup_deadline - time.monotonic()))
                # Sampler threads are created by the real supervisor, not F's
                # control list. Check its authentic returned handle too.
                require(all(not worker.is_alive() for worker in v.threads), "unsafe worker remains after rescue")
                require(observer.active_samplers == 0, "sampler worker remains after rescue")
                v.settle_case()
            except BaseException as exc:
                v.stopped = True
                record.update(status="FAIL", settlement_error=f"{type(exc).__name__}: {exc}")
            freeze_backend("after_fixture_settlement")  # actual final outcome, never backend acceptance
            record.update(safety_violations=list(v.safety_violations),
                          watchdog_rescues=[r for r in v.watchdog_rescues if r["scope"] == v.scope],
                          boundary_settled=v.boundary_settled, stopped=v.stopped)
            if v.safety_violations or v.stopped:
                record["status"] = "FAIL"
                record["stop_reason"] = "fixture safety violation or unknown boundary/worker settlement"
            record["failure_interval_complete"] = bool(
                record.get("finalizer", {}).get("watchdog_accepted") and v.boundary_settled
                and not v.stopped and not v.safety_violations and "settlement_error" not in record
                and (case != "C4-owner-isolation" or (
                    record.get("C_production_settlement", {}).get("complete") is True
                    and "C_normal_settlement_failure" not in record))
                and (not f1_case or (
                    record.get("F1_production_settlement", {}).get("complete") is True
                    and "F1_normal_settlement_failure" not in record)))
            record["sensitivity"] = "UNASSESSED: requires named target/source attribution; FAIL is never GREEN"
            # Retain completed cleanup-path events as separately labelled data;
            # they cannot replace missing before-rescue backend proof.
            record["after_rescue_event_range"] = [start_index, len(v.events)]
    return record


def main():
    invocation_started = time.monotonic()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--scratch-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=("baseline", "candidate"), required=True)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--case", choices=CASES, help="one existing variant, with both normal C5 inputs")
    selection.add_argument("--selection", choices=tuple(SELECTIONS),
                           help="accepted B/H/K/W/T/C/R/E/L/F1 selection, or explicit S/U negative diagnostic")
    args = parser.parse_args()
    # Validate selectors BEFORE even the source-admission Git subprocesses,
    # imports, scratch creation, readiness or fixture/backend probe.
    if args.mode == "baseline" and (args.case is not None or args.selection not in (None, "B")):
        parser.error("baseline permits only its immutable two-case B selection")
    if args.mode == "candidate" and args.selection == "B":
        parser.error("B requires --mode baseline and the exact accepted pre-fix HEAD")
    cases = ((args.case,) if args.case else SELECTIONS[args.selection] if args.selection
             else BASELINE_CASES if args.mode == "baseline" else CASES)
    diagnostic = args.selection if args.selection in ("S", "U") else None
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
                          census=process_census, mode=args.mode, selection=args.selection)
    require(args.scratch_root.is_absolute() and args.output.is_absolute(), "absolute scratch/output required")
    args.scratch_root.mkdir(parents=True, exist_ok=True)
    import tempfile
    scratch = Path(tempfile.mkdtemp(prefix="lifecycle-", dir=args.scratch_root))
    v = Venue(scratch, args.output,
              15 + 30 * len(cases) + (4 if "C4-owner-isolation" in cases else 0) + 15,
              invocation_started)
    v.diagnostic = diagnostic
    v.report.update({"mode": args.mode, "source": str(source),
                     "selection": args.selection, "selected_cases": cases, "diagnostic": diagnostic,
                     "head": source_head,
                     "tree": subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=source, text=True).strip(),
                     "python": sys.version, "python_executable": sys.executable,
                     "verifier_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                     "source_sha256": {name: hashlib.sha256((source / name).read_bytes()).hexdigest()
                         for name in ("runtime/orchestrator/host_supervisor.py", "runtime/platform/macos_process_group.py",
                                      "runtime/platform/process_census.py")},
                     "native_journal": "SOURCE-MAPPED ONLY; UNPROVEN", "historical_ownership": "UNPROVEN"})
    if any(case in F1_CASES for case in cases):
        loaded = {}
        for name in v.report["source_sha256"]:
            module = sys.modules[name[:-3].replace("/", ".")]
            path = Path(module.__file__).resolve(strict=True)
            require(path == (source / name).resolve(strict=True), "F1 loaded runtime path mismatch")
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            require(digest == v.report["source_sha256"][name], "F1 loaded runtime byte mismatch")
            loaded[name] = {"path": str(path), "sha256": digest, "bytes": len(raw),
                            "mode": oct(path.stat().st_mode & 0o777)}
        v.report["F1_loaded_runtime"] = loaded
    try:
        v.readiness()
        executable = scratch / "provider"
        executable.write_text("#!" + sys.executable + "\n" + PROVIDER)
        executable.chmod(0o700)
        v.sentinel("S")
        v.sentinel("J")
        v.report["readiness"] = "PASS"
        for case in cases:
            v.case_action_limit_seconds = 24 if case == "C4-owner-isolation" else 20
            v.case_deadline = min(time.monotonic() + v.case_action_limit_seconds, v.rescue_deadline)
            if case == "C5-unreadable":
                inputs = []
                for fault in ("permission", "cap"):
                    if v.stopped:
                        break
                    inputs.append(run_case(v, case, api, fault))
                v.results.append({"case": case, "status": "PASS" if len(inputs) == 2 and all(r["status"] == "PASS" for r in inputs) else "FAIL",
                                  "declared_inputs": ["permission", "cap"],
                                  "unexecuted_inputs": [f for f in ("permission", "cap")
                                                        if f not in [r["input"] for r in inputs]],
                                  "inputs": inputs})
            else:
                v.results.append(run_case(v, case, api))
            if v.stopped:
                v.report["stopped_after"] = dict(v.scope)
                break
            if case in F1_CASES and v.results[-1]["status"] != "PASS":
                v.report["halted_after_F1_failure"] = dict(v.scope)
                break
            # Baseline expected product failures do not authorize retries.
    except BaseException as exc:
        v.report.update(fatal=f"{type(exc).__name__}: {exc}", trace=traceback.format_exc(limit=6))
    finally:
        try:
            v.close()
            v.report["fixture_cleanup"] = ("RESIDUAL/UNKNOWN: earlier worker settlement unknown"
                                           if v.report.get("worker_settlement_unknown") else "SETTLED")
        except BaseException as exc:
            v.report["fixture_cleanup"] = f"RESIDUAL/UNKNOWN: {exc}"
        v.report.update(elapsed_seconds=round(time.monotonic() - v.started, 6), peaks=v.peaks,
                        scratch=str(scratch), boot_id=v.boot, stopped=v.stopped)
        size = sum(p.stat().st_size for p in scratch.rglob("*") if p.is_file())
        v.report["scratch_bytes"] = size
        require(size <= 67108864, "scratch cap exceeded")
        expected = len(cases)
        v.report["all_cases_present"] = len(v.results) == expected
        v.report["status"] = "PASS" if (len(v.results) == expected and all(r["status"] == "PASS" for r in v.results)
            and v.report.get("fixture_cleanup") == "SETTLED" and "fatal" not in v.report
            and not v.safety_violations and not v.stopped) else "FAIL"
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
