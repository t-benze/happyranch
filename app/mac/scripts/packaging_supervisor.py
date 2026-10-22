#!/usr/bin/env python3
"""RETIRED / NONSHIPPING under Founder THR104 seq369.

Historical implementation only; Mac build and all shipping readers do not import
or invoke this file. Its former protocol is not a current acceptance gate.

Packaging-local deadlines and direct-child ownership, not OS containment.

A child number permits signaling ONLY while its actual parent holds the unreaped
handle. Registration on the private channel never transfers that permission.
Kernel I/O, parent loss and escaped/unregistered descendants remain unknown.
"""
from __future__ import annotations

import contextlib
import errno
import fcntl
import json
import os
from pathlib import Path
import select
import signal
import socket
import stat
import subprocess
import sys
import time

# Closed production inventory. There is no caller-selected timeout or retry.
DEADLINES = {
    'asset-token-field': 30, 'asset-begin': 600, 'asset-check-public': 600,
    'asset-observe-dist': 600, 'asset-check-dist': 600, 'asset-observe-app': 600,
    'asset-icon': 600, 'asset-verify': 600,
    'freezer-token-field': 30, 'freezer-begin': 600, 'freezer-accept': 600,
    'freezer-before-copy': 600, 'freezer-copy': 600, 'freezer-verify': 600,
    'freezer-fail': 30, 'web-token-field': 30, 'web-read-input': 120,
    'web-compile-begin': 120, 'web-compile-end': 120, 'web-snapshot': 120,
    'web-prepare': 300, 'web-begin': 300, 'web-publish-build': 600,
    'web-complete-build': 600, 'web-accept-build': 600, 'web-seal-dist': 600,
    'web-notices': 600, 'web-verify': 600, 'web-fail': 30,
    'embedded-notices': 600, 'manifest-collect': 600, 'manifest-verify': 600,
    'verifier-preflight': 300, 'verifier-native': 300, 'verifier-resources': 300,
    'verifier-catalog': 300, 'verifier-trust': 300, 'verifier-inventory': 300,
    'verifier-archive': 300, 'verifier-reconcile': 300, 'verifier-licenses': 600,
    'failure-diagnostics': 60, 'frozen-entrypoints': 600,
    'internal-failure-persist': 30, 'build-log-finalize': 10,
}
# Sample before guardian/logger allocate any private descriptors.
try:
    os.fstat(3)
    DIAGNOSTIC_FD3 = True
except OSError:
    DIAGNOSTIC_FD3 = False
CONTROL_ENV = '_HAPPYRANCH_PACKAGING_CONTROL'
CHUNK = 65536


class LifecycleError(ValueError):
    pass


def ceiling(operation):
    try:
        return DEADLINES[operation]
    except KeyError:
        raise LifecycleError('PACKAGING_HELPER_OPERATION_UNKNOWN') from None


@contextlib.contextmanager
def signals(on_first=None):
    """Record once; handlers cannot unwind launch, disarm, reap or closure."""
    first, saved = [], {}
    def record(sig, _frame):
        if not first:
            first.append(sig)
            if on_first is not None:
                on_first(sig)
    try:
        for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
            saved[sig] = signal.getsignal(sig)
            signal.signal(sig, record)
        yield first
    finally:
        for sig, handler in saved.items():
            signal.signal(sig, handler)


class Control:
    """One bounded private exchange, charged to the original owner's clock."""
    def __init__(self):
        self.sock = None
        raw = os.environ.get(CONTROL_ENV)
        if raw is not None:
            fd = os.dup(int(raw))
            try:
                self.sock = socket.socket(fileno=fd)
            except BaseException:
                os.close(fd)
                raise
        self.deadline = None
        self.local_deadline = None

    def exchange(self, action, **data):
        if self.sock is None:
            return  # Unsupervised import: no external deadline claim.
        stop = time.monotonic() + 1
        if self.deadline is not None:
            stop = min(stop, self.deadline)
        if self.local_deadline is not None:
            stop = min(stop, self.local_deadline)
        self.sock.settimeout(max(0.000001, stop - time.monotonic()))
        raw = json.dumps(dict(action=action, **data), separators=(',', ':')).encode()
        if len(raw) > 512:
            raise LifecycleError('PACKAGING_CONTROL_CAP')
        self.sock.send(raw)
        reply = json.loads(self.sock.recv(512))
        if reply.get('action') != action or reply.get('epoch') != data.get('epoch') or reply.get('ok') is not True:
            raise LifecycleError('PACKAGING_CONTROL_REFUSED')
        deadline = reply.get('deadline')
        if not isinstance(deadline, (int, float)) or time.monotonic() >= min(deadline, self.local_deadline or deadline):
            raise LifecycleError('PACKAGING_CONTROL_DEADLINE')
        self.deadline = deadline

    def close(self):
        if self.sock is not None:
            self.sock.close()
            self.sock = None


@contextlib.contextmanager
def failure_scope(prior_status=1):
    """Clip real persistence at the outer owner, preserving its primary cause.

    Unsupervised imports have no external preemption guarantee.
    """
    control = Control()
    entered = False
    primary = None
    try:
        control.exchange('failure', prior=int(prior_status or 1))
        entered = True
        yield
    except BaseException as exc:
        primary = exc
        raise
    finally:
        try:
            if entered:
                control.exchange('failure-end')
        except BaseException as secondary:
            if primary is None:
                raise
            primary.add_note('PACKAGING_FAILURE_SCOPE_CLOSE_UNKNOWN')
        finally:
            control.close()


class Child:
    """Serialized waiter. No Popen.poll/send_signal hidden inside signaling."""
    def __init__(self, control=None):
        if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
            raise LifecycleError('PACKAGING_SIGCHLD_UNSUPPORTED')
        self.proc = None
        self.pin = False
        self.launched = False
        self.ambiguous = False
        self.control = control
        self.epoch = None

    def launch(self, argv, **kwargs):
        self.proc = subprocess.Popen.__new__(subprocess.Popen)
        try:
            self.proc.__init__(argv, **kwargs)
        except BaseException:
            # __init__ may already have reaped an acquired PID. Never arm it.
            self.ambiguous = True
            raise
        self.launched = self.pin = True
        self.arm()
        return self.proc

    def arm(self):
        if not self.pin or self.ambiguous:
            raise LifecycleError('PACKAGING_CHILD_NOT_HELD')
        self.epoch = time.monotonic_ns()
        if self.control:
            self.control.exchange('register', epoch=self.epoch, pid=self.proc.pid)

    def disarm(self):
        # Local gate falls BEFORE a fallible release or any poll/wait.
        self.pin = False
        epoch, self.epoch = self.epoch, None
        if self.control and epoch is not None:
            try:
                self.control.exchange('release', epoch=epoch)
            except BaseException:
                # No reap has been attempted: local cleanup still holds the
                # identity. This is NOT re-registration or cross-parent takeover.
                if self.launched and not self.ambiguous and self.proc.returncode is None:
                    self.pin = True
                raise

    def poll(self):
        self.disarm()
        try:
            if self.proc.returncode is not None:
                status = self.proc.returncode
            else:
                pid, raw = os.waitpid(self.proc.pid, os.WNOHANG)
                status = None if pid == 0 else os.waitstatus_to_exitcode(raw)
                if status is not None:
                    self.proc.returncode = status
        except BaseException:
            self.ambiguous = True
            raise
        if status is None:
            self.pin = True
            self.arm()
        return status

    def kill(self, sig):
        if not self.pin or not self.launched or self.ambiguous:
            raise LifecycleError('PACKAGING_CHILD_NOT_HELD')
        try:
            os.killpg(self.proc.pid, sig)
        except ProcessLookupError:
            pass

    def reap(self, cutoff):
        release_error = None
        try:
            self.disarm()
        except BaseException as exc:
            release_error = exc
        self.pin = False  # Even a lost release acknowledgement cannot span reap.
        if not self.launched:
            # Best-effort direct-child observation only; never reacquire a pin.
            pid = getattr(self.proc, 'pid', None)
            if type(pid) is int and pid > 0:
                try:
                    observed, raw = os.waitpid(pid, os.WNOHANG)
                    if observed == pid:
                        self.proc.returncode = os.waitstatus_to_exitcode(raw)
                except OSError:
                    pass
            raise LifecycleError('PACKAGING_CHILD_CONSTRUCTOR_UNKNOWN')
        try:
            code = self.proc.returncode
            while code is None:
                pid, raw = os.waitpid(self.proc.pid, os.WNOHANG)
                if pid:
                    code = self.proc.returncode = os.waitstatus_to_exitcode(raw)
                    break
                if time.monotonic() >= cutoff:
                    raise LifecycleError('PACKAGING_REAP_UNKNOWN')
                time.sleep(min(0.01, max(0, cutoff - time.monotonic())))
        except BaseException:
            self.ambiguous = True
            raise
        if release_error:
            raise release_error
        return code

    def absent(self):
        # Observation only. There is deliberately no post-reap kill.
        try:
            os.killpg(self.proc.pid, 0)
        except ProcessLookupError:
            return True
        except OSError:
            pass
        return False

    def settle(self, eof, absent):
        if self.control:
            self.control.exchange('settled', eof=bool(eof), reaped=self.proc.returncode is not None,
                                  absent=bool(absent))


def finish_status(code):
    if code < 0:
        sig = -code
        signal.signal(sig, signal.SIG_DFL)
        if hasattr(signal, 'pthread_sigmask'):
            signal.pthread_sigmask(signal.SIG_UNBLOCK, {sig})
        os.kill(os.getpid(), sig)
        return 128 + sig
    return code


def _diagnostic(message, cutoff=None):
    """One nonblocking attempt; secondary output never changes the result.

    Readiness is only a hint when stderr aliases another sink. Do not wait or
    retry here, or acquire another clock after the owner's cleanup window.
    """
    old = None
    try:
        if cutoff is not None and time.monotonic() >= cutoff:
            return
        old = fcntl.fcntl(2, fcntl.F_GETFL)
        os.set_blocking(2, False)
        os.write(2, message)
    except OSError:
        pass
    finally:
        if old is not None:
            try:
                fcntl.fcntl(2, fcntl.F_SETFL, old)
            except OSError:
                pass


class _FailureAttribution:
    """Two advisory attempts, each <=256 bytes, separate from custody grants.

    Consume each slot before acquisition/output, including failed attempts.
    Neither classification nor a secondary sink error may replace the cause.
    """
    def __init__(self, owner):
        self.owner = 'inspection' if owner == 'inspection' else 'relay'
        self.first_attempted = False
        self.terminal_attempted = False

    def first(self, phase, exc, cutoff):
        try:
            if self.first_attempted:
                return
            self.first_attempted = True
            if phase not in ('setup', 'launch', 'relay', 'inspect', 'kill',
                             'reap', 'settle', 'close', 'flags'):
                phase = 'unknown'
            category = 'unknown'
            for kind, label in ((LifecycleError, 'lifecycle'),
                                (subprocess.TimeoutExpired, 'timeout'),
                                (OSError, 'os'), (ValueError, 'value'),
                                (subprocess.SubprocessError, 'subprocess'),
                                (KeyboardInterrupt, 'interrupt'), (SystemExit, 'exit')):
                if isinstance(exc, kind):
                    category = label
                    break
            number = exc.errno if isinstance(exc, OSError) else None
            number = number if type(number) is int and 0 < number <= 2147483647 else 0
            message = (f'PACKAGING_HELPER_FIRST_ERROR owner={self.owner} phase={phase} '
                       f'category={category} errno={number}\n').encode('ascii')
            if len(message) <= 256:
                _diagnostic(message, cutoff)
        except BaseException:
            pass

    def terminal(self, child, code, cutoff, *, control_unknown=False, nested=False,
                 complete=False, absent=False, error=False, timeout=False, interrupted=False):
        try:
            if self.terminal_attempted:
                return
            self.terminal_attempted = True
            # Read only the parent's already-stored observation; never poll.
            observed = child.proc.returncode if child is not None and child.launched else None
            message = (f'PACKAGING_HELPER_SETTLEMENT owner={self.owner} '
                       f'control_unknown={int(control_unknown)} nested={int(nested)} '
                       f'complete={int(complete)} absent={int(absent)} code_known={int(code is not None)} '
                       f'child_known={int(observed is not None)} child_zero={int(observed == 0)} '
                       f'child_nonzero={int(observed is not None and observed != 0)} '
                       f'child_signal={int(observed is not None and observed < 0)} '
                       f'error={int(error)} timeout={int(timeout)} interrupted={int(interrupted)}\n').encode('ascii')
            if len(message) <= 256:
                _diagnostic(message, cutoff)
        except BaseException:
            pass


def _fifo(path):
    fd = os.open(path, os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        if not stat.S_ISFIFO(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o600:
            raise LifecycleError('PACKAGING_FIFO_UNSAFE')
        return fd
    except BaseException:
        os.close(fd)
        raise


def relay(operation, argv, *, origin=None, cancel_fd=None, guardian=False,
          logger=False, inherited=()):
    """Streaming relay; at most one CHUNK per channel, no payload-size grant.

    The sole5s cleanup clock includes control, drain and reap. Guardian and
    logger use their specified independent fallback/finalization schedules.
    """
    started = time.monotonic() if origin is None else origin
    deadline = started + ceiling(operation)
    if guardian:
        deadline += 10
    if logger:
        deadline = float('inf')  # Ten seconds starts at finalization, not build.
    child = Child()
    fds, flags, owned_ids = [], {}, set()
    left = right = None
    incoming = bytearray()
    outputs = {}  # reader -> [sink, pending, eof]
    in_read = in_write = None
    input_eof = False
    cause = {}  # setdefault latches atomically even inside a signal handler.
    cleanup = None
    kill_at = drain_at = None
    killed = False
    nested = None
    prior = None
    failure_deadline = None
    control_unknown = False
    code = None
    finalizing = False
    logger_term = None
    logger_termed = False
    attribution = _FailureAttribution('relay')
    phase = 'setup'

    def remember(fd):
        fds.append(fd)
        owned_ids.add(fd)
        return fd

    def close(fd):
        if fd in fds:
            fds.remove(fd)
            os.close(fd)

    def nonblocking(fd):
        if fd not in flags:
            flags[fd] = fcntl.fcntl(fd, fcntl.F_GETFL)
        os.set_blocking(fd, False)

    def observed_status(fallback):
        # Child.poll/reap store decoded wait status before their caller receives
        # it, including a reap that subsequently raises a release error.
        observed = child.proc.returncode if child.launched else None
        return prior if prior is not None else observed if observed not in (None, 0) else fallback

    def enter(reason, now):
        nonlocal cleanup, kill_at, drain_at, killed
        reason = cause.setdefault('status', observed_status(reason))
        if cleanup is not None:
            return
        cleanup = min(now + (1 if guardian and reason == 124 else 10 if guardian or logger else 5),
                      deadline + (1 if guardian else 5))
        grace = 0 if guardian and reason == 124 else 7 if guardian or logger else 2
        kill_at = min(now + grace, cleanup - 2 if not guardian else cleanup - 0.5)
        drain_at = min(kill_at + 1, cleanup - 1)
        if child.pin:
            child.kill(-reason if reason < 0 else signal.SIGTERM)
        if grace == 0 and child.pin:
            child.kill(signal.SIGKILL)
            killed = True

    def record_signal(sig):
        # No I/O or cleanup from the handler. A child failure already observed
        # by this owner (or an existing primary cause) precedes a late signal.
        reason = observed_status(-sig)
        cause.setdefault('status', reason)

    with signals(record_signal) as caught:
        try:
            # Snapshot aliases before changing ANY borrowed open-file flags.
            for fd in (0, 1, 2, *((3,) if DIAGNOSTIC_FD3 else ())):
                flags[fd] = fcntl.fcntl(fd, fcntl.F_GETFL)
            left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
            left.setblocking(False)
            env = dict(os.environ)
            env[CONTROL_ENV] = str(right.fileno())
            # No child sees caller-facing pipes. fd3 is proxied only if granted.
            pass_fds = [right.fileno(), *inherited]
            in_read, in_write = os.pipe()
            remember(in_read); remember(in_write)
            out_read, out_write = os.pipe()
            remember(out_read); remember(out_write)
            err_read, err_write = os.pipe()
            remember(err_read); remember(err_write)
            outputs[out_read] = [1, bytearray(), False]
            outputs[err_read] = [2, bytearray(), False]
            diag_write = None
            try:
                os.fstat(3)
            except OSError:
                pass
            else:
                # fd3 may be one of OUR new descriptors if caller had no fd3.
                if DIAGNOSTIC_FD3:
                    diag_read, diag_write = os.pipe()
                    remember(diag_read); remember(diag_write)
                    outputs[diag_read] = [3, bytearray(), False]
                    pass_fds.append(diag_write)
            command = argv
            if diag_write is not None:
                command = [sys.executable, str(Path(__file__).resolve()), '_exec', str(diag_write), *argv]
            phase = 'launch'
            child.launch(command, stdin=in_read, stdout=out_write, stderr=err_write,
                         env=env, start_new_session=True, pass_fds=tuple(pass_fds))
            phase = 'relay'
            right.close(); right = None
            for fd in (in_read, out_write, err_write, diag_write):
                if fd is not None:
                    close(fd)
            for fd in (0, in_write, *outputs):
                nonblocking(fd)
            for sink, _, _ in outputs.values():
                nonblocking(sink)
            while True:
                now = time.monotonic()
                if caught:
                    enter(-caught[0], now)
                if cancel_fd is not None:
                    try:
                        message = os.read(cancel_fd, 4)
                    except BlockingIOError:
                        message = b''
                    if message:
                        if logger and not finalizing:
                            finalizing = True
                            deadline = now + 10
                            logger_term = now + 7
                        elif not logger:
                            number = {b'H': signal.SIGHUP, b'I': signal.SIGINT, b'T': signal.SIGTERM}.get(message[:1])
                            if number:
                                enter(-number, now)
                if logger_term is not None and now >= logger_term and not logger_termed:
                    logger_termed = True
                    cause.setdefault('status', 125)
                    cleanup, kill_at, drain_at = deadline, logger_term + 1, deadline - 1
                    if child.pin:
                        child.kill(signal.SIGTERM)
                effective = min(deadline, failure_deadline or deadline)
                if now >= effective:
                    secondary = 'status' not in cause and prior is not None
                    enter(prior if prior is not None else 124, now)
                    if secondary:
                        _diagnostic(('PACKAGING_HELPER_SECONDARY_DEADLINE:' + operation + '\n').encode(), cleanup)
                if cleanup is not None and now >= kill_at and not killed:
                    if child.pin:
                        child.kill(signal.SIGKILL)
                    killed = True
                if cleanup is not None and now >= min(drain_at, cleanup):
                    break
                all_eof = all(row[2] for row in outputs.values())
                pending = any(row[1] for row in outputs.values())
                if all_eof and not pending:
                    # No poll while a descendant can still hold output open.
                    code = child.poll()
                    if code is not None:
                        break
                read = [left.fileno()]
                write = []
                if cleanup is None and not input_eof and len(incoming) < CHUNK:
                    read.append(0)
                if incoming and in_write in fds:
                    write.append(in_write)
                for fd, (sink, data, eof) in outputs.items():
                    if not eof and len(data) < CHUNK:
                        read.append(fd)
                    if data:
                        write.append(sink)
                readable, writable, _ = select.select(read, write, [], min(0.05, max(0, effective - now)) if cleanup is None else 0.05)
                if left.fileno() in readable:
                    raw = left.recv(513)
                    if not raw or len(raw) > 512:
                        raise LifecycleError('PACKAGING_CONTROL_LOST')
                    msg = json.loads(raw)
                    action, epoch = msg.get('action'), msg.get('epoch')
                    if action == 'register' and nested is None and isinstance(epoch, int) and type(msg.get('pid')) is int:
                        nested = epoch
                    elif action == 'release' and nested == epoch:
                        nested = None
                    elif action == 'settled' and nested is None:
                        if not all(msg.get(k) is True for k in ('eof', 'reaped', 'absent')):
                            control_unknown = True
                    elif action == 'failure' and type(msg.get('prior')) is int and failure_deadline is None:
                        if prior is None:
                            prior = msg['prior'] or 1
                        failure_deadline = min(deadline, now + 30)
                    elif action == 'failure-end':
                        failure_deadline = None
                        # Original prior failure remains latched for later cleanup.
                    else:
                        raise LifecycleError('PACKAGING_CONTROL_INVALID')
                    bound = min(deadline, failure_deadline or deadline, cleanup or deadline)
                    left.send(json.dumps(dict(action=action, epoch=epoch, ok=True, deadline=bound), separators=(',', ':')).encode())
                if 0 in readable:
                    try:
                        chunk = os.read(0, CHUNK - len(incoming))
                    except BlockingIOError:
                        chunk = None  # Another alias may have consumed readiness.
                    if chunk:
                        incoming.extend(chunk)
                    if chunk == b'':
                        input_eof = True
                        if logger and not finalizing:
                            finalizing = True
                            deadline = now + 10
                            logger_term = now + 7
                if in_write in writable:
                    try:
                        count = os.write(in_write, incoming)
                        if count <= 0:
                            raise LifecycleError('PACKAGING_RELAY_WRITE')
                        del incoming[:count]
                    except BlockingIOError:
                        pass  # Retain all bytes for the next readiness check.
                    except BrokenPipeError:
                        incoming.clear(); input_eof = True
                        close(in_write)
                if input_eof and not incoming and in_write in fds:
                    close(in_write)
                for fd, row in outputs.items():
                    sink, data, eof = row
                    if fd in readable:
                        try:
                            chunk = os.read(fd, CHUNK - len(data))
                        except BlockingIOError:
                            chunk = None
                        if chunk:
                            data.extend(chunk)
                        elif chunk == b'':
                            row[2] = True
                    if sink in writable and data:
                        try:
                            count = os.write(sink, data)
                        except BlockingIOError:
                            # stdout/stderr/fd3 can share a pipe: an earlier
                            # write may have consumed the same readiness.
                            continue
                        if count <= 0:
                            raise LifecycleError('PACKAGING_RELAY_WRITE')
                        del data[:count]
        except BaseException as exc:
            attribution.first(phase, exc, cleanup if cleanup is not None else deadline)
            control_unknown = True
            try:
                enter(prior if prior is not None else 125, time.monotonic())
            except BaseException as exc:
                attribution.first('kill', exc, cleanup if cleanup is not None else deadline)
                pass
        finally:
            # Independent attempts; never rearm after ambiguous poll or reap.
            if child.pin:
                try:
                    child.kill(signal.SIGKILL)
                except BaseException as exc:
                    attribution.first('kill', exc, cleanup if cleanup is not None else deadline)
                    control_unknown = True
            stop = cleanup or (time.monotonic() + 1)
            if child.proc is not None:
                try:
                    code = child.reap(stop)
                except BaseException as exc:
                    attribution.first('reap', exc, stop)
                    control_unknown = True
            for fd in tuple(fds):
                try:
                    close(fd)
                except OSError as exc:
                    attribution.first('close', exc, stop)
                    control_unknown = True
            for sock in (left, right):
                if sock is not None:
                    try:
                        sock.close()
                    except OSError as exc:
                        attribution.first('close', exc, stop)
                        control_unknown = True
            if guardian and killed:
                control_unknown = True  # Worker loss supplies no nested-helper settlement.
            complete = bool(outputs) and all(row[2] and not row[1] for row in outputs.values())
            absent = child.proc is not None and child.launched and child.absent()
            unresolved = control_unknown or nested is not None or not complete or not absent or code is None
            status = cause.get('status', observed_status(code))
            if status in (None, 0) and unresolved:
                status = cause.setdefault('status', prior if prior is not None else 125)
            diagnostic_stop = min(stop, cleanup if cleanup is not None else deadline)
            if status == 124:
                _diagnostic(('PACKAGING_HELPER_DEADLINE:' + operation + '\n').encode(), diagnostic_stop)
            if unresolved:
                attribution.terminal(child, code, diagnostic_stop,
                                     control_unknown=control_unknown, nested=nested is not None,
                                     complete=complete, absent=absent)
                _diagnostic(('PACKAGING_HELPER_CLEANUP_UNRESOLVED:' + operation + '\n').encode(), diagnostic_stop)
            # Restore borrowed stdio after terminal diagnostics. A failed restore
            # may attempt its own nonblocking, independently restored advisory.
            for fd, old in flags.items():
                if fd not in owned_ids:  # borrowed caller descriptors only
                    try:
                        fcntl.fcntl(fd, fcntl.F_SETFL, old)
                    except OSError as exc:
                        attribution.first('flags', exc, diagnostic_stop)
                        control_unknown = True
            if status in (None, 0) and control_unknown:
                status = cause.setdefault('status', prior if prior is not None else 125)
    # Handlers are restored before the final read: signals recorded during
    # diagnostics, flag restoration or handler restoration still participate.
    return int(cause.get('status', status))


def inspection_run(argv):
    """Original verifier run(): inherited stdin/env, text capture and60s.

    No new output cap is imposed on the original subprocess.run contract.
    """
    import io
    start = time.monotonic()
    control = None
    child = None
    owned_fds = set()
    fds, output = [], [bytearray(), bytearray()]
    eof = [False, False]
    code = None
    timeout = False
    error = None
    cutoff = start + 60
    attribution = _FailureAttribution('inspection')
    phase = 'setup'
    absent = False
    with signals() as caught:
        try:
            control = Control()
            control.local_deadline = cutoff
            child = Child(control)
            for _ in range(2):
                fds.extend(os.pipe())
                owned_fds.update(fds)
            phase = 'launch'
            child.launch(argv, stdin=None, stdout=fds[1], stderr=fds[3], start_new_session=True)
            phase = 'inspect'
            for i in (1, 3):
                owned_fds.remove(fds[i])
                os.close(fds[i])
            readers = {fds[0]: 0, fds[2]: 1}
            for fd in readers:
                os.set_blocking(fd, False)
            cleaning = False
            while True:
                now = time.monotonic()
                if (caught or now >= start + 60) and not cleaning:
                    timeout = not caught
                    cleaning = True
                    cutoff = now + 5
                    control.local_deadline = cutoff
                    if child.pin:
                        child.kill(caught[0] if caught else signal.SIGTERM)
                    kill_at, drain_at = now + 2, now + 3
                if cleaning and now >= kill_at:
                    if child.pin:
                        child.kill(signal.SIGKILL)
                    kill_at = float('inf')
                if cleaning and now >= drain_at:
                    break
                if not readers:
                    code = child.poll()
                    if code is not None:
                        break
                ready, _, _ = select.select(list(readers), [], [], 0.05)
                for fd in ready:
                    raw = os.read(fd, CHUNK)
                    if raw:
                        output[readers[fd]].extend(raw)
                    else:
                        eof[readers.pop(fd)] = True
        except BaseException as exc:
            attribution.first(phase, exc, cutoff)
            error = exc
        finally:
            if child is not None:
                try:
                    if child.pin:
                        child.kill(signal.SIGKILL)
                except BaseException as exc:
                    attribution.first('kill', exc, cutoff)
                    error = error or exc
                try:
                    code = child.reap(min(cutoff, time.monotonic() + 1))
                except BaseException as exc:
                    attribution.first('reap', exc, cutoff)
                    error = error or exc
                try:
                    absent = child.launched and child.absent()
                    child.settle(all(eof), absent)
                    if not absent or not all(eof):
                        raise LifecycleError('PACKAGING_INSPECTION_CLEANUP_UNKNOWN')
                except BaseException as exc:
                    attribution.first('settle', exc, cutoff)
                    error = error or exc
            for fd in tuple(owned_fds):
                owned_fds.remove(fd)
                try:
                    os.close(fd)
                except OSError as exc:
                    attribution.first('close', exc, cutoff)
                    error = error or exc
            if control is not None:
                try:
                    control.close()
                except OSError as exc:
                    attribution.first('close', exc, cutoff)
                    error = error or exc
            if error is not None or timeout or caught:
                attribution.terminal(child, code, cutoff, complete=all(eof), absent=absent,
                                     error=error is not None, timeout=timeout, interrupted=bool(caught))
        if caught:
            raise LifecycleError('PACKAGING_INSPECTION_INTERRUPTED:' + str(caught[0]))
    if timeout:
        raise subprocess.TimeoutExpired(argv, 60, output=bytes(output[0]), stderr=bytes(output[1]))
    if error:
        raise error
    # TextIOWrapper preserves locale decoding and universal-newline conversion.
    text = [io.TextIOWrapper(io.BytesIO(raw)).read() for raw in output]
    return subprocess.CompletedProcess(argv, code, *text)


def main():
    if len(sys.argv) < 2:
        raise LifecycleError('PACKAGING_HELPER_ARGUMENTS')
    mode, *args = sys.argv[1:]
    if mode == '_exec':
        fd = int(args[0])
        os.dup2(fd, 3, inheritable=True)
        if fd != 3:
            os.close(fd)
        os.execvpe(args[1], args[1:], os.environ)
    if mode == '_worker':
        operation, fd, *argv = args
        origin = float(os.read(int(fd), 64).decode())
        os.close(int(fd))
        if origin > time.monotonic():
            raise LifecycleError('PACKAGING_CLOCK_INVALID')
        return relay(operation, argv, origin=origin)
    if mode == 'run':
        operation, *argv = args
        if argv[:1] == ['--']:
            argv = argv[1:]
        return relay(operation, argv)
    if mode == 'guardian':
        operation, control_path, result_path, *argv = args
        origin = time.monotonic()
        ceiling(operation)
        control = result = clock_read = clock_write = None
        try:
            control = _fifo(control_path)
            result = _fifo(result_path)
            clock_read, clock_write = os.pipe()
            os.write(clock_write, repr(origin).encode())
            os.close(clock_write); clock_write = None
            command = [sys.executable, str(Path(__file__).resolve()), '_worker', operation, str(clock_read), *argv]
            status = relay(operation, command, origin=origin, cancel_fd=control,
                           guardian=True, inherited=(clock_read,))
            os.write(result, ('RESULT ' + str(status) + '\n').encode())
            return status
        finally:
            for fd in (control, result, clock_read, clock_write):
                if fd is not None:
                    os.close(fd)
    if mode == 'logger':
        control_path, result_path, *argv = args
        control = result = None
        try:
            control = _fifo(control_path)
            result = _fifo(result_path)
            status = relay('build-log-finalize', argv, cancel_fd=control, logger=True)
            os.write(result, ('RESULT ' + str(status) + '\n').encode())
            return status
        finally:
            for fd in (control, result):
                if fd is not None:
                    os.close(fd)
    raise LifecycleError('PACKAGING_HELPER_MODE_UNKNOWN')


if __name__ == '__main__':
    try:
        result = main()
    except (OSError, ValueError, LifecycleError):
        result = 125
        _diagnostic(b'PACKAGING_HELPER_LIFECYCLE_ERROR\n')
    raise SystemExit(finish_status(result))
