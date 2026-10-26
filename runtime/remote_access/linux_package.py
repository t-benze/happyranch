"""Deterministic Linux composite package for the managed embedded transport."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
import zipfile
import subprocess
import stat
import sys
import time
from typing import Callable, Mapping

from runtime.remote_access.systemd_unit import ConnectorUnitSpec, render_connector_unit


class PackageError(RuntimeError):
    """Stable, category-only package failure."""


PREFIX = "happyranch-linux-amd64"
UNITS = (
    "happyranch-connector.service",
    "happyranch-tsnet-sidecar.service",
    "happyranch-managed.target",
)
PAYLOAD_MODES = {
    "bin/happyranch-tsnet-sidecar": "0o755",
    "bin/happyranch-connector": "0o755",
    "share/happyranch.whl": "0o644",
    "share/dependency-inventory.json": "0o644",
    "share/sbom.cdx.json": "0o644",
    "share/THIRD_PARTY_NOTICES.md": "0o644",
    **{f"systemd/{name}": "0o644" for name in UNITS},
}
TRANSACTION_MARKER = ".happyranch-install-transaction.json"
# Bounded on-disk install transaction contract.  The marker is the sole durable
# ownership record: it is written atomically before any prior (OLD) byte is
# mutated and is removed last, after the authoritative commit and cleanup.  A
# record exists in exactly one of the phases below and records the minimum facts
# needed to classify and recover or conservatively refuse the transaction.
TRANSACTION_SCHEMA_VERSION = 2
_PAYLOAD_BACKUP_NAME = ".happyranch-backup"
_UNIT_BACKUP_NAME = ".happyranch-units-backup"
_STAGE_PREFIX = ".happyranch-stage-"
_DROPIN_SERVICE_DIR = "happyranch-tsnet-sidecar.service.d"
_DROPIN_FILE_NAME = "10-enrollment-credential.conf"
_DROPIN_BACKUP_RELATIVE = PurePosixPath(_DROPIN_SERVICE_DIR) / _DROPIN_FILE_NAME
_RESIDUE_PREFIXES = (
    _PAYLOAD_BACKUP_NAME,
    _UNIT_BACKUP_NAME,
    _STAGE_PREFIX,
    ".happyranch-install-transaction",
)
_DROPIN_BYTES = b"[Service]\nLoadCredential=enrollment.key:/etc/happyranch/enrollment.key\n"
# A record is produced only by this module: the attempt identity is a 32-char
# lowercase hex token and the stage directory name is the ``mkdtemp`` product
# of ``.happyranch-stage-<attempt>-<8 random chars>``.  Requiring that exact
# shape (rather than a bare prefix) prevents a plausible marker from
# redirecting ownership to an unrelated foreign directory.
_ATTEMPT_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_STAGE_SUFFIX_PATTERN = re.compile(r"^[a-z0-9_]{8}$")
# Finite, source-derived plan of parents the installer may create.  A record
# may only ever claim to own parents from this closed set, so an arbitrary
# entry can never authorize deleting a pre-existing empty directory.
_CREATED_PARENT_PLAN = (
    "opt",
    "etc",
    "etc/systemd",
    "etc/systemd/system",
)
_TRANSACTION_KEYS = frozenset({
    "schema_version", "attempt_id", "root", "phase", "payload_present",
    "units", "dropin_present", "stage", "created_parents",
    "published_units", "dropin_published", "backups",
    "new_payload", "new_units", "new_dropin",
})
_TRANSACTION_PHASES = frozenset({
    "preparing", "prepared", "payload_retained", "payload_published",
    "units_publishing", "dropin_publishing", "rolling_back", "committed",
})



MANAGED_LIFECYCLE_CONTRACT = {"lifecycle": 2, "child_health": 2, "recovery_diagnostics": 2}
_MANAGED_EXECUTABLE = "/opt/happyranch/bin/happyranch-tsnet-sidecar"


# Private operation context, never a public bypass flag. Only the fixed child
# below installs it after authenticating the inherited locked description.
from contextlib import contextmanager
from contextvars import ContextVar
import fcntl
import selectors
import signal

_MANAGED_DEADLINE: ContextVar[int | None] = ContextVar("managed_deadline", default=None)
_MANAGED_FRESH: ContextVar[bool] = ContextVar("managed_fresh", default=False)
_MANAGED_PACKAGE: ContextVar[bool] = ContextVar("managed_package", default=False)
_MANAGED_LOCK: ContextVar[int | None] = ContextVar("managed_lock", default=None)
# Private direct-package receipt custody; never serialized by public APIs.
_MANAGED_PACKAGE_DIAGNOSTIC: ContextVar[dict[str, object] | None] = ContextVar("managed_package_diagnostic", default=None)
_MANAGED_DECLARATION: ContextVar[dict[str, int] | None] = ContextVar("managed_declaration", default=None)


def _managed_entry_deadline(seconds: int, *, entry_ns: int | None = None) -> int:
    now = time.monotonic_ns()
    inherited = _MANAGED_DEADLINE.get()
    limit = (now if entry_ns is None else entry_ns) + seconds * 1_000_000_000
    deadline = min(inherited if inherited is not None else limit, limit)
    carrier = os.environ.get("HAPPYRANCH_LIFECYCLE_DEADLINE_NS")
    if carrier is not None:
        if re.fullmatch(r"[1-9][0-9]{0,18}", carrier) is None or int(carrier) > 2**63 - 1:
            raise PackageError("managed_lifecycle_deadline")
        deadline = min(deadline, int(carrier))
    if deadline <= now:
        raise PackageError("managed_lifecycle_deadline")
    return deadline


def _check_managed_holder() -> None:
    descriptor = _MANAGED_LOCK.get()
    deadline = _MANAGED_DEADLINE.get()
    if descriptor is None or deadline is None or time.monotonic_ns() >= deadline:
        raise PackageError("managed_lifecycle_context_required")
    observed = os.fstat(descriptor)
    named = Path("/var/lib/happyranch-connector/lifecycle.lock").lstat()
    if (not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1
            or observed.st_size != 0 or stat.S_IMODE(observed.st_mode) != 0o600
            or (observed.st_dev, observed.st_ino, observed.st_uid) != (named.st_dev, named.st_ino, named.st_uid)):
        raise PackageError("managed_lifecycle_context_required")
    independent = os.open("/var/lib/happyranch-connector/lifecycle.lock", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        try:
            fcntl.flock(independent, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            raise PackageError("managed_lifecycle_context_required")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(independent)
    if time.monotonic_ns() >= deadline:
        raise PackageError("managed_lifecycle_deadline")


def _require_managed_package_context() -> None:
    if not _MANAGED_PACKAGE.get():
        raise PackageError("managed_package_context_required")
    _check_managed_holder()


def _spawn_fixed_lifecycle(executable: str, operation: str, request: dict[str, object],
                           descriptor_map: Mapping[int, int], *,
                           _package_diagnostic: dict[str, object] | None = None) -> bytes:
    """Closed executable entry and internal descriptors, with actual child reap.

    Expiry refuses; it never unlocks a borrowed flock. A surviving package
    writer retains its own inherited descriptor even if this gate is killed.
    Spawn-action duplicates belong only to the parent until spawn returns;
    close them before waiting for output, without closing borrowed originals.
    Relinquish ownership before close: Linux may release the FD even when
    close raises, so retrying could close a reused descriptor.
    Final close errors refuse only when no body or kill/reap error is already
    propagating; all remaining owned closes are still attempted.
    """
    if (executable not in {_MANAGED_EXECUTABLE, "/proc/self/fd/5", "/proc/self/fd/6"}
            or executable == "/proc/self/fd/5" and operation != "managed-package"
            or executable == "/proc/self/fd/6" and operation != "managed-package-check"
            or executable != _MANAGED_EXECUTABLE and operation == "managed-lifecycle"
            or operation not in {"managed-package", "managed-run-check", "managed-stopped-check", "managed-package-check", "managed-lifecycle"}):
        raise PackageError("managed_lifecycle_operation_invalid")
    deadline = request["deadline_ns"]
    if type(deadline) is not int or deadline <= time.monotonic_ns():
        raise PackageError("managed_lifecycle_deadline")
    raw = json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
    if len(raw) > 4096 or time.monotonic_ns() >= deadline:
        raise PackageError("managed_lifecycle_refused")
    diagnostic = (_package_diagnostic if _package_diagnostic is not None else {}) if operation == "managed-package" else None
    # Exact full lines only; nested metadata is validated as one closed record.
    reasons = {
        "entry_context", "effective_uid", "operation", "incoming_image",
        "admission_deadline", "executable_open", "bootstrap_operation",
        "bootstrap_pair", "open_pair", "admission_current", "admission_child",
        "inhibit_policy", "inhibit_deadline", "manager_stop", "reacquire_pair",
        "stopped_admission_deadline", "stopped_current", "stopped_admission_child",
        "retire_policy", "package_body", "final_refresh", "final_current",
    }
    lines = {b"managed_package_refusal:" + reason.encode("ascii") + b"\n": reason
             for reason in reasons}
    private_reasons = {
        "effective_uid", "deadline", "context_before", "entry_input",
        "admission_contract", "admission_history", "admission_dropin",
        "uninstall_prefix", "uninstall_images", "admission_context_after",
        "body_guard_before", "body_history", "body_contract", "body_install",
        "body_uninstall", "body_context_after", "body_guard_after", "body_response",
        "context_release",
    }
    def nested_record(raw: bytes) -> tuple[str, dict[str, object]] | None:
        prefix = b"managed_package_child_refusal:"
        if not raw.startswith(prefix) or not raw.endswith(b"\n"):
            return None
        try:
            record = json.loads(raw[len(prefix):-1], object_pairs_hook=_reject_duplicate_keys)
        except (ValueError, UnicodeError, PackageError, RecursionError):
            return None
        if (type(record) is not dict or set(record) != {"reason", "child"}
                or type(record["reason"]) is not str
                or record["reason"] not in {"admission_child", "stopped_admission_child", "package_body"}):
            return None
        child = record["child"]
        if (type(child) is not dict or set(child) != {
                "reason", "diagnostic_state", "helper_exit", "deadline_elapsed",
                "kill_requested", "reaped", "reap_exit"}
                or type(child["diagnostic_state"]) is not str
                or child["diagnostic_state"] not in {
                    "observed", "absent", "unavailable", "malformed", "unallowlisted", "truncated", "contradictory"}
                or any(type(child[k]) is not bool for k in ("deadline_elapsed", "kill_requested", "reaped"))
                or any(child[k] is not None and (type(child[k]) is not int or not -64 <= child[k] <= 255)
                       for k in ("helper_exit", "reap_exit"))
                or child["helper_exit"] != child["reap_exit"]
                or not child["reaped"] and child["helper_exit"] is not None):
            return None
        if child["diagnostic_state"] == "observed":
            if (type(child["reason"]) is not str or child["reason"] not in private_reasons
                    or not child["reaped"] or child["helper_exit"] != 78):
                return None
        elif child["reason"] is not None:
            return None
        return record["reason"], child

    frame_limit = 1024
    frame = bytearray()
    diagnostic_bytes = diagnostic_lines = 0
    diagnostic_eof = False
    diagnostic_loss: str | None = None
    frame_overflow = False
    if diagnostic is not None:
        diagnostic.update(reason=None, diagnostic_state="unavailable", helper_exit=None,
                          deadline_elapsed=None, reaped=False, outcome="refused",
                          kill_requested=False, reap_exit=None, child=None)
    source_read = source_write = output_read = output_write = null = -1
    error_read = error_write = -1
    duplicates: list[int] = []
    child: int | None = None
    unwinding = False
    try:
        source_read, source_write = os.pipe2(os.O_CLOEXEC)
        output_read, output_write = os.pipe2(os.O_CLOEXEC)
        null = os.open(os.devnull, os.O_RDWR | os.O_CLOEXEC)
        if diagnostic is not None:
            error_read, error_write = os.pipe2(os.O_CLOEXEC)
        mapping = {0: source_read, 1: output_write,
                   2: error_write if diagnostic is not None else null, **descriptor_map}
        actions = []
        for target, source in mapping.items():
            duplicate = fcntl.fcntl(source, fcntl.F_DUPFD_CLOEXEC, 10)
            duplicates.append(duplicate)
            actions.append((os.POSIX_SPAWN_DUP2, duplicate, target))
        environment = {k: v for k, v in os.environ.items() if k != "NOTIFY_SOCKET"}
        if time.monotonic_ns() >= deadline:
            raise PackageError("managed_lifecycle_deadline")
        child = os.posix_spawn(executable, [executable, operation], environment, file_actions=actions)
        while duplicates:
            os.close(duplicates.pop())
        descriptor, output_write = output_write, -1
        os.close(descriptor)
        if error_write >= 0:
            descriptor, error_write = error_write, -1
            os.close(descriptor)
        if time.monotonic_ns() >= deadline or os.write(source_write, raw) != len(raw):
            raise PackageError("managed_lifecycle_refused")
        descriptor, source_write = source_write, -1
        os.close(descriptor)
        os.set_blocking(output_read, False)
        result = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(output_read, selectors.EVENT_READ)
            if diagnostic is not None:
                os.set_blocking(error_read, False)
                selector.register(error_read, selectors.EVENT_READ)
            while selector.get_map():
                remaining = (deadline - time.monotonic_ns()) / 1_000_000_000
                if remaining <= 0:
                    raise PackageError("managed_lifecycle_deadline")
                ready = selector.select(remaining)
                if not ready:
                    raise PackageError("managed_lifecycle_deadline")
                for key, _events in ready:
                    is_output = key.fd == output_read
                    piece = os.read(key.fd, 4097 - len(result) if is_output else 256)
                    if not piece:
                        selector.unregister(key.fd)
                        if not is_output:
                            diagnostic_eof = True
                            if frame or frame_overflow:
                                diagnostic_loss = diagnostic_loss or "malformed"
                        continue
                    if is_output:
                        result.extend(piece)
                        if len(result) > 4096:
                            raise PackageError("managed_lifecycle_response_invalid")
                    else:
                        diagnostic_bytes += len(piece)
                        if diagnostic_bytes + len(result) > 4096:
                            diagnostic_loss = "truncated"
                            diagnostic.update(reason=None, child=None)
                            frame.clear()
                            # Stop after one bounded overflow read; no unlimited
                            # stderr drain or detached pipe beyond the deadline.
                            selector.unregister(error_read)
                            descriptor, error_read = error_read, -1
                            os.close(descriptor)
                            continue
                        # Transient input only; never retain arbitrary stderr.
                        for byte in piece:
                            if diagnostic_loss == "truncated":
                                break
                            if len(frame) < frame_limit:
                                frame.append(byte)
                            else:
                                frame_overflow = True
                            if byte == 10:
                                diagnostic_lines += 1
                                if frame_overflow:
                                    diagnostic_loss = diagnostic_loss or "malformed"
                                else:
                                    closed = nested_record(bytes(frame))
                                    if bytes(frame) in lines:
                                        closed = (lines[bytes(frame)], None)
                                    if closed is None:
                                        state = ("unallowlisted" if all(32 <= c <= 126 for c in frame[:-1])
                                                 else "malformed")
                                        diagnostic_loss = diagnostic_loss or state
                                    elif diagnostic_lines == 1 and diagnostic_loss is None:
                                        diagnostic.update(reason=closed[0], child=closed[1])
                                    else:
                                        diagnostic_loss = diagnostic_loss or "contradictory"
                                frame.clear()
                                frame_overflow = False
                    if diagnostic is not None and diagnostic_bytes + len(result) > 4096:
                        diagnostic_loss = "truncated"
                        diagnostic.update(reason=None, child=None)
                        frame.clear()
        while True:
            reaped, status = os.waitpid(child, os.WNOHANG)
            if reaped:
                child = None
                helper_exit = os.waitstatus_to_exitcode(status)
                elapsed = time.monotonic_ns() >= deadline
                if diagnostic is not None:
                    diagnostic.update(helper_exit=helper_exit, deadline_elapsed=elapsed,
                                      reaped=True, reap_exit=helper_exit)
                if helper_exit != 0 or elapsed:
                    raise PackageError("managed_lifecycle_refused")
                if diagnostic is not None:
                    diagnostic["outcome"] = "success"
                return bytes(result)
            if time.monotonic_ns() >= deadline:
                raise PackageError("managed_lifecycle_deadline")
            time.sleep(max(0, min(0.01, (deadline - time.monotonic_ns()) / 1_000_000_000)))
    except BaseException as exc:
        unwinding = True
        if diagnostic is not None:
            if diagnostic["deadline_elapsed"] is None:
                diagnostic["deadline_elapsed"] = time.monotonic_ns() >= deadline
            if isinstance(exc, PackageError):
                diagnostic["outcome"] = {
                    "managed_lifecycle_deadline": "deadline",
                    "managed_lifecycle_response_invalid": "response_invalid",
                }.get(str(exc), "refused")
        raise
    finally:
        try:
            if child is not None:
                # This is our unreaped child, not a rediscovered service PID.
                try:
                    if diagnostic is not None:
                        diagnostic["kill_requested"] = True
                    os.kill(child, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                # No detached success or timed unlock. If kernel exit lags, the
                # caller remains the original owner until this actual reap.
                _pid, status = os.waitpid(child, 0)
                if diagnostic is not None:
                    diagnostic.update(reaped=True, reap_exit=os.waitstatus_to_exitcode(status))
        except BaseException:
            unwinding = True
            raise
        finally:
            remaining = [source_read, source_write, output_read, output_write,
                         error_read, error_write, null, *duplicates]
            duplicates.clear()
            close_error: OSError | None = None
            while remaining:
                descriptor = remaining.pop()
                if descriptor >= 0:
                    try:
                        os.close(descriptor)
                    except OSError as exc:
                        # Never retry a possibly released FD; still attempt all
                        # other owned closes, including after kill/reap errors.
                        if close_error is None:
                            close_error = exc
            if diagnostic is not None:
                if diagnostic["deadline_elapsed"] is None:
                    diagnostic["deadline_elapsed"] = time.monotonic_ns() >= deadline
                if frame or frame_overflow:
                    diagnostic_loss = diagnostic_loss or "malformed"
                if not diagnostic_eof:
                    diagnostic_loss = diagnostic_loss or "unavailable"
                if diagnostic["helper_exit"] == 0 and diagnostic["reason"] is not None:
                    diagnostic_loss = diagnostic_loss or "contradictory"
                if diagnostic["child"] is not None and diagnostic["helper_exit"] != 78:
                    diagnostic_loss = diagnostic_loss or "contradictory"
                if diagnostic_loss is not None:
                    diagnostic.update(reason=None, child=None, diagnostic_state=diagnostic_loss)
                else:
                    diagnostic["diagnostic_state"] = "observed" if diagnostic["reason"] is not None else "absent"
                if close_error is not None and not unwinding:
                    diagnostic["outcome"] = "refused"
            if close_error is not None and not unwinding:
                raise PackageError("managed_lifecycle_refused") from close_error


def _managed_guard_probe(kind: str, descriptor: int, deadline: int) -> None:
    request = {"version": 2, "operation": kind, "operation_id": secrets.token_hex(16), "deadline_ns": deadline}
    executable = "/proc/self/fd/6" if kind == "package" else _MANAGED_EXECUTABLE
    mapping = {3: descriptor, 6: 6} if kind == "package" else {3: descriptor}
    _spawn_fixed_lifecycle(executable, "managed-" + kind + "-check", request, mapping)
    if time.monotonic_ns() >= deadline:
        raise PackageError("managed_lifecycle_deadline")


@contextmanager
def managed_body_guard(kind: str, *, entry_ns: int | None = None):
    """BODY-owned fixed helper exclusion, including direct Python calls."""
    if kind not in {"run", "stopped"}:
        raise PackageError("managed_lifecycle_operation_invalid")
    previous = _MANAGED_LOCK.get()
    deadline = _managed_entry_deadline(60 if kind == "stopped" else 120, entry_ns=entry_ns)
    if kind == "run":
        # This observation ONLY shortens preparation/lock waiting. The Go
        # guard still proves the actual current ControlPID ancestry and the
        # independent installed/loaded reader still supplies authority.
        from runtime.remote_access.cli import _capture_bounded_query_output, _close_and_reap_query_process
        bound = min(deadline / 1_000_000_000, time.monotonic() + 1)
        if time.monotonic() >= bound:
            raise PackageError("managed_lifecycle_deadline")
        child = subprocess.Popen(["/usr/bin/systemctl", "show", "happyranch-tsnet-sidecar.service",
                                  "--property=InvocationID", "--property=InactiveExitTimestampMonotonic"],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 pass_fds=(() if previous is None else (previous,)),
                                 env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"})
        try:
            raw, overflow = _capture_bounded_query_output(child, bound)
            remaining = bound - time.monotonic()
            if overflow or remaining <= 0 or child.wait(timeout=remaining) != 0:
                raise PackageError("managed_lifecycle_refused")
        finally:
            _close_and_reap_query_process(child, bound, managed=True)
        try:
            rows = raw.decode("ascii").split("\n")
            values = dict(row.split("=", 1) for row in rows[:-1])
        except ValueError as exc:
            raise PackageError("managed_lifecycle_refused") from exc
        if (len(rows) != 3 or rows[-1] or set(values) != {"InvocationID", "InactiveExitTimestampMonotonic"}
                or values["InvocationID"] != os.environ.get("INVOCATION_ID")
                or re.fullmatch(r"[0-9a-f]{32}", values["InvocationID"]) is None
                or values["InvocationID"] == "0" * 32
                or re.fullmatch(r"[1-9][0-9]{0,15}", values["InactiveExitTimestampMonotonic"]) is None):
            raise PackageError("managed_lifecycle_refused")
        activation = int(values["InactiveExitTimestampMonotonic"]) * 1000
        now = time.monotonic_ns()
        deadline = min(deadline, activation + 120_000_000_000)
        if activation > now or now >= deadline or time.monotonic() >= bound:
            raise PackageError("managed_lifecycle_deadline")
    if previous is not None:
        deadline_token = _MANAGED_DEADLINE.set(deadline)
        try:
            _check_managed_holder()
            declaration = _installed_managed_contract(deadline_ns=deadline)
            declaration_token = _MANAGED_DECLARATION.set(declaration)
            _managed_guard_probe(kind, previous, deadline)
            _check_managed_holder()
            try:
                yield deadline
            finally:
                _check_managed_holder()
                _installed_managed_contract(deadline_ns=deadline)
                _managed_guard_probe(kind, previous, deadline)
                _check_managed_holder()
        finally:
            if "declaration_token" in locals():
                _MANAGED_DECLARATION.reset(declaration_token)
            _MANAGED_DEADLINE.reset(deadline_token)
        return
    descriptor = os.open("/var/lib/happyranch-connector/lifecycle.lock", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    deadline_token = _MANAGED_DEADLINE.set(deadline)
    lock_token = _MANAGED_LOCK.set(descriptor)
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic_ns() >= deadline:
                    raise PackageError("managed_lifecycle_deadline")
                time.sleep(0.01)
        _check_managed_holder()
        declaration = _installed_managed_contract(deadline_ns=deadline)
        declaration_token = _MANAGED_DECLARATION.set(declaration)
        _managed_guard_probe(kind, descriptor, deadline)
        _check_managed_holder()
        try:
            yield deadline
        finally:
            # Failure paths retain the same holder, and cannot skip the final
            # deadline/current-owner check merely because the body raised.
            _check_managed_holder()
            _installed_managed_contract(deadline_ns=deadline)
            _managed_guard_probe(kind, descriptor, deadline)
            _check_managed_holder()
    finally:
        if "declaration_token" in locals():
            _MANAGED_DECLARATION.reset(declaration_token)
        # No LOCK_UN: borrowed descriptions held by reload/query descendants
        # retain exclusion until their actual completion, even on parent loss.
        _MANAGED_LOCK.reset(lock_token)
        _MANAGED_DEADLINE.reset(deadline_token)
        os.close(descriptor)


def _package_sealing_constants() -> tuple[int, int, int, int]:
    # CPython exports depend on build headers, including on supported 3.12-3.14.
    # Linux UAPI: asm-generic/fcntl.h base=1024; linux/fcntl.h ADD=base+9,
    # GET=base+10, SEAL/SHRINK/GROW/WRITE=1/2/4/8; linux/memfd.h flags=1/2.
    # These are Linux ABI arguments only, never an emulation of missing support.
    if sys.platform != "linux":
        raise PackageError("package_input_invalid")
    required = (getattr(fcntl, "F_SEAL_WRITE", 0x0008)
                | getattr(fcntl, "F_SEAL_GROW", 0x0004)
                | getattr(fcntl, "F_SEAL_SHRINK", 0x0002)
                | getattr(fcntl, "F_SEAL_SEAL", 0x0001))
    flags = getattr(os, "MFD_CLOEXEC", 0x0001) | getattr(os, "MFD_ALLOW_SEALING", 0x0002)
    return (getattr(fcntl, "F_ADD_SEALS", 1033),
            getattr(fcntl, "F_GET_SEALS", 1034), required, flags)


def _managed_package_outer(package: Path | None, operation: str, deadline: int) -> dict[str, object]:
    if os.geteuid() != 0 or operation not in {"package-install", "package-uninstall"}:
        raise PackageError("managed_package_context_required")
    if operation == "package-install":
        require_credential_capability(Path("/etc/happyranch/daemon.token"), expected_uid=os.geteuid())
        enrollment = Path("/etc/happyranch/enrollment.key")
        if _backup_present(enrollment):
            require_credential_capability(enrollment, expected_uid=os.geteuid())
    descriptors: list[int] = []

    def sealed(name: str, raw: bytes, executable: bool = False) -> int:
        add_seals, _get_seals, required, flags = _package_sealing_constants()
        create = getattr(os, "memfd_create", None)
        if not callable(create):
            raise PackageError("package_input_invalid")
        try:
            descriptor = create(name, flags)
            descriptors.append(descriptor)
            view = memoryview(raw)
            while view:
                count = os.write(descriptor, view)
                if count <= 0:
                    raise PackageError("package_input_invalid")
                view = view[count:]
            os.fchmod(descriptor, 0o755 if executable else 0o600)
            os.lseek(descriptor, 0, os.SEEK_SET)
            fcntl.fcntl(descriptor, add_seals, required)
        except OSError as exc:
            raise PackageError("package_input_invalid") from exc
        return descriptor

    unwinding = False
    try:
        if package is not None:
            # Authenticate ONE immutable incoming image before using its two
            # fixed existing executables. No extraction/temp/stage precedes B.
            _require_package_source(package)
            incoming = sealed("happyranch-incoming-package", package.read_bytes())
            files, _manifest = _read_verified(Path(f"/proc/self/fd/{incoming}"))
            connector = sealed("happyranch-connector", files["bin/happyranch-connector"], True)
            executable = sealed("happyranch-tsnet-sidecar", files["bin/happyranch-tsnet-sidecar"], True)
            mapping = {3: incoming, 4: connector, 5: executable}
            path = "/proc/self/fd/5"
        else:
            # Uninstall still needs the fixed incoming slots, never arbitrary
            # payload selection. These are the current installed executables.
            _installed_managed_contract(deadline_ns=deadline, _package=True)
            prefix, _units, _dropin, _empty_prior = _package_managed_history(deadline)
            if prefix is None:
                raise PackageError("managed_lifecycle_incompatible")
            incoming = sealed("happyranch-unused-package", b"unused")
            connector = sealed("happyranch-connector", (prefix / "bin/happyranch-connector").read_bytes(), True)
            executable = sealed("happyranch-tsnet-sidecar", (prefix / "bin/happyranch-tsnet-sidecar").read_bytes(), True)
            mapping = {3: incoming, 4: connector, 5: executable}
            path = "/proc/self/fd/5"
        request = {"version": 2, "operation": operation, "operation_id": secrets.token_hex(16), "deadline_ns": deadline}
        raw = _spawn_fixed_lifecycle(path, "managed-package", request, mapping,
                                     _package_diagnostic=_MANAGED_PACKAGE_DIAGNOSTIC.get())
        try:
            result = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
            if not isinstance(result, dict):
                raise PackageError("managed_lifecycle_response_invalid")
        except BaseException:
            diagnostic = _MANAGED_PACKAGE_DIAGNOSTIC.get()
            if diagnostic is not None:
                diagnostic["outcome"] = "response_invalid"
            raise
        return result
    except BaseException:
        unwinding = True
        raise
    finally:
        close_error: OSError | None = None
        while descriptors:
            # These originals belong to this caller, not the spawn helper.
            # Relinquish before close; never retry a possibly reused number.
            descriptor = descriptors.pop()
            try:
                os.close(descriptor)
            except OSError as exc:
                if close_error is None:
                    close_error = exc
        if close_error is not None and not unwinding:
            diagnostic = _MANAGED_PACKAGE_DIAGNOSTIC.get()
            if diagnostic is not None:
                diagnostic["outcome"] = "refused"
            raise PackageError("managed_lifecycle_refused") from close_error


def _nonmanaged_owner_destination(path: Path) -> Path:
    """Resolve an existing destination or a missing tail from a proven parent.

    Missing files may be created by the existing generic writer. A dangling
    link, inaccessible parent, non-directory ancestor or unresolved parent
    component is unknown ownership, never permission to stage over it.
    """
    missing: list[str] = []
    current = Path(path)
    try:
        while True:
            try:
                current.lstat()
            except FileNotFoundError:
                if current.parent == current or current.name in {"", ".", ".."}:
                    raise PackageError("managed_lifecycle_owner_unknown")
                missing.append(current.name)
                current = current.parent
                continue
            resolved = current.resolve(strict=True)
            if missing and not resolved.is_dir():
                raise PackageError("managed_lifecycle_owner_unknown")
            return resolved.joinpath(*reversed(missing))
    except (OSError, RuntimeError, ValueError) as exc:
        raise PackageError("managed_lifecycle_owner_unknown") from exc


def _nonmanaged_owner_same_path(left: Path, right: Path) -> bool:
    if left == right:
        return True
    identities = []
    for path in (left, right):
        try:
            metadata = path.stat()
        except FileNotFoundError:
            identities.append(None)
        except OSError as exc:
            raise PackageError("managed_lifecycle_owner_unknown") from exc
        else:
            identities.append((metadata.st_dev, metadata.st_ino))
    return identities[0] is not None and identities[0] == identities[1]


def require_nonmanaged_unit_owner(unit_name: str, unit_dir: Path, *,
                                  user_unit_dir: Path | None = None) -> None:
    """Fence generic destinations; default callers retain local-marker refusal.

    The generic USER manager may identify its existing canonical unit directory.
    Ordinary units there are not local package markers merely by their names.
    This context never releases fixed identities, state custody or other paths.
    """
    # State custody is independent of unit-name/marker classification. A fixed
    # local name with absent markers still cannot write into either state tree.
    _require_nonmanaged_state_owner(Path(unit_dir) / unit_name)
    directory = _nonmanaged_owner_destination(unit_dir)
    destination = _nonmanaged_owner_destination(Path(unit_dir) / unit_name)
    fixed_directory = _nonmanaged_owner_destination(Path("/etc/systemd/system"))
    fixed_units = tuple(_nonmanaged_owner_destination(fixed_directory / name) for name in UNITS)
    local_units = tuple(_nonmanaged_owner_destination(directory / name) for name in UNITS)
    fixed = any(_nonmanaged_owner_same_path(destination, path) for path in fixed_units)
    local = any(_nonmanaged_owner_same_path(destination, path) for path in local_units)
    if not (fixed or local):
        return
    # Choose markers from the actual owner directory. An alternate spelling
    # of the fixed directory must never suppress its durable lifecycle marker.
    fixed = fixed or _nonmanaged_owner_same_path(directory, fixed_directory)
    owner_directory = fixed_directory if fixed else directory
    if (not fixed and user_unit_dir is not None
            and Path(unit_dir) == directory == user_unit_dir):
        # A positively separate canonical USER namespace may contain any of
        # these ordinary unit names, including siblings installed sequentially.
        # Recheck concrete custody, including after write; system=False alone
        # cannot release markers in an arbitrary writer/fragment directory.
        _require_nonmanaged_state_owner(Path(unit_dir) / unit_name,
                                        staging=True, directory=False)
        return
    markers = (owner_directory / UNITS[1], owner_directory / UNITS[2])
    if fixed:
        markers += (Path("/var/lib/happyranch-connector/lifecycle.json"),
                    Path("/var/lib/happyranch-tsnet-sidecar/lifecycle.json"))
    for path in markers:
        marker = _nonmanaged_owner_destination(path)
        try:
            marker.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise PackageError("managed_lifecycle_owner_unknown") from exc
        raise PackageError("managed_lifecycle_owner_required")


def _require_nonmanaged_state_owner(root: Path, *, staging: bool = False,
                                     directory: bool | None = None) -> None:
    """Exclude concrete destinations from BOTH fixed state trees.

    Only local metadata is used. Multiple links to a regular inode cannot
    establish independent custody without searching protected contents, so
    refuse them. Staging additionally requires canonical, nonlinked ordinary
    files/directories; this is exclusion, never a managed-authority grant.
    """
    destination = _nonmanaged_owner_destination(root)
    if staging and (".." in Path(root).parts
                    or destination != Path(os.path.abspath(root))):
        raise PackageError("managed_lifecycle_owner_unknown")
    for fixed_root in (Path("/var/lib/happyranch-connector"),
                       Path("/var/lib/happyranch-tsnet-sidecar")):
        fixed = _nonmanaged_owner_destination(fixed_root)
        # Ancestors exclude recursive ownership of a containing directory;
        # existing directory inodes also cover equivalent parent spellings.
        overlaps = any(_nonmanaged_owner_same_path(parent, fixed)
                       for parent in (destination, *destination.parents))
        overlaps = overlaps or any(_nonmanaged_owner_same_path(destination, parent)
                                   for parent in fixed.parents)
        left, right = destination, fixed
        while left.name == right.name and left.parent != left and right.parent != right:
            left, right = left.parent, right.parent
            overlaps = overlaps or _nonmanaged_owner_same_path(left, right)
        if overlaps:
            # Missing lifecycle/unit markers never release fixed state custody.
            raise PackageError("managed_lifecycle_owner_required")
    try:
        metadata = Path(root).lstat() if staging else destination.stat()
    except FileNotFoundError:
        # _nonmanaged_owner_destination already proved the existing parent and
        # refused dangling links. Only an ordinary missing tail can be created.
        return
    except OSError as exc:
        raise PackageError("managed_lifecycle_owner_unknown") from exc
    regular, is_directory = stat.S_ISREG(metadata.st_mode), stat.S_ISDIR(metadata.st_mode)
    if (not (regular or is_directory) or (regular and metadata.st_nlink != 1)
            or (directory is True and not is_directory)
            or (directory is False and not regular)):
        raise PackageError("managed_lifecycle_owner_unknown")


def managed_lifecycle(operation: str, *, expected_installation: str | None = None,
                      expected_revision: int | None = None, operation_id: str | None = None,
                      deadline_ns: int | None = None) -> dict[str, object]:
    """The one fixed owner seam for existing lifecycle verbs and reconcile.

    A caller may shorten an existing deadline; no retry or error unwind gets a
    new allowance. IDs are generated once here and never automatically replayed.
    """
    entry_ns = time.monotonic_ns()
    if operation not in {"start", "stop", "restart", "enable", "disable", "status", "reconcile"}:
        raise PackageError("managed_lifecycle_operation_invalid")
    deadline = _managed_entry_deadline(45, entry_ns=entry_ns)
    if deadline_ns is not None:
        deadline = min(deadline, deadline_ns)
    if deadline <= time.monotonic_ns():
        raise PackageError("managed_lifecycle_deadline")
    request = {"version": 2, "operation": operation, "expected_installation": expected_installation,
               "expected_revision": expected_revision, "operation_id": operation_id or secrets.token_hex(16),
               "deadline_ns": deadline}
    try:
        raw = _spawn_fixed_lifecycle(_MANAGED_EXECUTABLE, "managed-lifecycle", request, {})
        report = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
        if (not isinstance(report, dict) or set(report) != {"version", "state", "operation_id"}
                or type(report["version"]) is not int or report["version"] != 2
                or report["operation_id"] != request["operation_id"]
                or report["state"] not in {"stopped", "boot_prepared", "starting", "ready", "retiring", "deferred", "rate_exhausted_until", "blocked", "unavailable"}):
            raise PackageError("managed_lifecycle_response_invalid")
        if time.monotonic_ns() >= deadline:
            raise PackageError("managed_lifecycle_deadline")
        return report
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise PackageError("managed_lifecycle_refused") from exc


def credential_capability(
    source: Path,
    *,
    expected_uid: int | None,
    allowed_modes: tuple[int, ...] | None = (0o600,),
    require_read_only: bool = False,
) -> str:
    """Classify credential usability without exposing paths, bytes, or OS errors."""
    path = Path(source)
    try:
        current = path
        while current != current.parent:
            metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                return "credential_unsafe_symlink"
            current = current.parent
    except FileNotFoundError:
        return "credential_absent"
    except OSError:
        return "credential_staging_incompatible"
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            return "credential_wrong_type"
        if expected_uid is not None and metadata.st_uid != expected_uid:
            return "credential_wrong_custody"
        if allowed_modes is not None and stat.S_IMODE(metadata.st_mode) not in allowed_modes:
            return "credential_wrong_custody"
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            if not os.read(descriptor, 1):
                return "credential_staging_incompatible"
        finally:
            os.close(descriptor)
        if require_read_only:
            try:
                descriptor = os.open(
                    path,
                    os.O_WRONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
                )
            except OSError:
                pass
            else:
                os.close(descriptor)
                return "credential_staging_incompatible"
    except OSError:
        return "credential_staging_incompatible"
    return "credential_valid"


def require_credential_capability(
    source: Path,
    *,
    expected_uid: int,
    allowed_modes: tuple[int, ...] = (0o600,),
) -> None:
    category = credential_capability(
        source, expected_uid=expected_uid, allowed_modes=allowed_modes
    )
    if category != "credential_valid":
        raise PackageError(category)


class CompositeServiceManager:
    """Injectable executable seam for the shipping composite systemd target."""

    def __init__(self, run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
                 systemctl: str = "systemctl") -> None:
        self._run, self._systemctl = run, systemctl

    def _call(self, *args: str) -> subprocess.CompletedProcess[str]:
        try:
            deadline = _MANAGED_DEADLINE.get()
            if deadline is None or time.monotonic_ns() >= deadline:
                raise PackageError("managed_lifecycle_deadline")
            argv = ["/usr/bin/systemctl" if self._systemctl == "systemctl" else self._systemctl, *args]
            result = self._run(argv, check=True, text=True, capture_output=True,
                               timeout=max(0, (deadline - time.monotonic_ns()) / 1_000_000_000),
                               env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
                               pass_fds=(() if _MANAGED_LOCK.get() is None else (_MANAGED_LOCK.get(),)))
            if time.monotonic_ns() >= deadline:
                raise PackageError("managed_lifecycle_deadline")
            return result
        except (OSError, subprocess.SubprocessError) as exc:
            raise PackageError("service_manager_failed") from exc

    def start_ready(self) -> None:
        deadline = _managed_entry_deadline(45)
        token = _MANAGED_DEADLINE.set(deadline)
        try:
            report = managed_lifecycle("start")
            if report["state"] != "ready":
                raise PackageError("service_not_ready")
            for unit in UNITS[:2]:
                result = self._call("show", unit, "--property=ActiveState", "--value")
                if result.stdout.strip() != "active":
                    raise PackageError("service_not_ready")
            if time.monotonic_ns() >= deadline:
                raise PackageError("managed_lifecycle_deadline")
        finally:
            _MANAGED_DEADLINE.reset(token)

    def stop(self) -> None:
        managed_lifecycle("stop")

    def restart_after_crash(self, unit: str) -> None:
        deadline = _managed_entry_deadline(45)
        token = _MANAGED_DEADLINE.set(deadline)
        try:
            if unit not in UNITS[:2]:
                raise PackageError("service_unit_invalid")
            managed_lifecycle("restart")
            result = self._call("show", unit, "--property=ActiveState", "--value")
            if result.stdout.strip() != "active":
                raise PackageError("service_not_ready")
            if time.monotonic_ns() >= deadline:
                raise PackageError("managed_lifecycle_deadline")
        finally:
            _MANAGED_DEADLINE.reset(token)


def render_composite_units(prefix: str = "/opt/happyranch") -> dict[str, str]:
    connector = render_connector_unit(ConnectorUnitSpec(
        exec_start=(f"{prefix}/bin/happyranch-tsnet-sidecar", "supervise-connector",
                    f"{prefix}/bin/happyranch-connector", "run", "--managed", "--config",
                    "/etc/happyranch/connector.json"),
        user="happyranch", group="happyranch",
        daemon_token_path="/etc/happyranch/daemon.token",
    )).replace(
        "After=network-online.target",
        "After=network-online.target\nWants=happyranch-tsnet-sidecar.service\n"
        "PartOf=happyranch-managed.target happyranch-tsnet-sidecar.service\nStartLimitIntervalSec=0",
    ).replace("RestartSec=1", "RestartSec=30").replace(
        "TimeoutStopSec=10", "TimeoutStartSec=120\nTimeoutStopSec=35\nTimeoutAbortSec=35\n"
        "KillMode=mixed\nWatchdogSignal=SIGUSR2\nRestartMode=normal\nRestartPreventExitStatus="
        + " ".join(str(code) for code in range(2, 256)),
    ).replace("WantedBy=multi-user.target", "WantedBy=happyranch-managed.target")
    sidecar = """[Unit]
Description=HappyRanch embedded tsnet sidecar
After=network-online.target
Wants=network-online.target
PartOf=happyranch-managed.target happyranch-connector.service
StartLimitIntervalSec=0

[Service]
Type=notify
NotifyAccess=main
ExecStartPre=+{prefix}/bin/happyranch-connector reconcile-enrollment-retirement --source /etc/happyranch/enrollment.key --marker /var/lib/happyranch-tsnet-sidecar/credential.consumed --dropin /etc/systemd/system/happyranch-tsnet-sidecar.service.d/10-enrollment-credential.conf
ExecStartPre={prefix}/bin/happyranch-connector credential-capability --name enrollment.key --unit happyranch-tsnet-sidecar.service --consumed-marker /var/lib/happyranch-tsnet-sidecar/credential.consumed
ExecStart={prefix}/bin/happyranch-tsnet-sidecar --config /etc/happyranch/sidecar.json
ExecStartPost=+{prefix}/bin/happyranch-connector retire-enrollment-source --source /etc/happyranch/enrollment.key --marker /var/lib/happyranch-tsnet-sidecar/credential.consumed --dropin /etc/systemd/system/happyranch-tsnet-sidecar.service.d/10-enrollment-credential.conf
User=happyranch
Group=happyranch
Restart=no
WatchdogSec=30
WatchdogSignal=SIGUSR2
KillMode=mixed
TimeoutStartSec=120
TimeoutStopSec=35
TimeoutAbortSec=35
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
RestrictRealtime=yes
LockPersonality=yes
MemoryDenyWriteExecute=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK
SystemCallArchitectures=native
CapabilityBoundingSet=
AmbientCapabilities=
UMask=0077
StateDirectory=happyranch-tsnet-sidecar
StateDirectoryMode=0700
RuntimeDirectory=happyranch-tsnet-sidecar
LogsDirectory=happyranch-tsnet-sidecar
[Install]
WantedBy=happyranch-managed.target
""".format(prefix=prefix)
    target = """[Unit]
Description=HappyRanch managed remote access composite
Wants=happyranch-connector.service happyranch-tsnet-sidecar.service
After=happyranch-connector.service happyranch-tsnet-sidecar.service

[Install]
WantedBy=multi-user.target
"""
    # The base renderer repeats RestartSec. Composite directives have one
    # selected value, including the private state-directory mode.
    lines = connector.splitlines()
    connector = "\n".join(line for index, line in enumerate(lines)
                            if line != "RestartSec=30" or line not in lines[:index]) + "\n"
    connector = connector.replace("StateDirectory=happyranch-connector\n",
                                  "StateDirectory=happyranch-connector\nStateDirectoryMode=0700\n")
    return {UNITS[0]: connector, UNITS[1]: sidecar, UNITS[2]: target}


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sbom(inventory: Mapping[str, object], version: str) -> bytes:
    modules = inventory.get("modules")
    if not isinstance(modules, list) or not modules:
        raise PackageError("inventory_invalid")
    components = []
    for item in modules:
        if not isinstance(item, dict):
            raise PackageError("inventory_invalid")
        try:
            components.append({
                "type": "library", "name": item["module"], "version": item["version"],
                "purl": f"pkg:golang/{item['module']}@{item['version']}",
                "licenses": [{"license": {"id": item["spdx"]}}],
                "properties": [
                    {"name": "happyranch:go.sum", "value": item["sum"]},
                    {"name": "happyranch:license-sha256", "value": item["license_sha256"]},
                ],
            })
        except KeyError as exc:
            raise PackageError("inventory_invalid") from exc
    payload = {"bomFormat": "CycloneDX", "specVersion": "1.5", "version": 1,
               "metadata": {"component": {"type": "application", "name": "happyranch-linux", "version": version}},
               "components": sorted(components, key=lambda item: (item["name"], item["version"]))}
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()


def build_linux_package(output: Path, sidecar: Path, connector: Path, wheel: Path,
                        inventory_path: Path, notices_path: Path, *, version: str) -> Path:
    try:
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        notices = notices_path.read_bytes()
        modules = inventory["modules"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise PackageError("package_input_invalid") from exc
    _validate_evidence(inventory, notices)
    if not sidecar.is_file() or not connector.is_file() or not zipfile.is_zipfile(wheel):
        raise PackageError("package_input_invalid")
    with zipfile.ZipFile(wheel) as built_wheel:
        if not any(name.startswith("runtime/") and name.endswith(".py") for name in built_wheel.namelist()):
            raise PackageError("wheel_invalid")
    units = render_composite_units()
    files: dict[str, tuple[bytes, int]] = {
        "bin/happyranch-tsnet-sidecar": (sidecar.read_bytes(), 0o755),
        "bin/happyranch-connector": (connector.read_bytes(), 0o755),
        "share/happyranch.whl": (wheel.read_bytes(), 0o644),
        "share/dependency-inventory.json": (inventory_path.read_bytes(), 0o644),
        "share/sbom.cdx.json": (_sbom(inventory, version), 0o644),
        "share/THIRD_PARTY_NOTICES.md": (notices, 0o644),
    }
    files.update({f"systemd/{name}": (text.encode(), 0o644) for name, text in units.items()})
    manifest = {"schema_version": 1, "version": version, "architecture": "linux-amd64",
                "sidecar_dependency_count": len(modules),
                "managed_lifecycle_contract": dict(MANAGED_LIFECYCLE_CONTRACT),
                "files": [{"path": name, "sha256": _sha(raw), "mode": oct(mode)}
                          for name, (raw, mode) in sorted(files.items())]}
    files["manifest.json"] = ((json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(), 0o644)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w", format=tarfile.PAX_FORMAT) as archive:
        for name, (raw, mode) in sorted(files.items()):
            info = tarfile.TarInfo(f"{PREFIX}/{name}")
            info.size, info.mode, info.mtime, info.uid, info.gid = len(raw), mode, 0, 0, 0
            info.uname = info.gname = "root"
            archive.addfile(info, io.BytesIO(raw))
    return output


def _validate_evidence(inventory: Mapping[str, object], notices: bytes) -> None:
    try:
        text = notices.decode("utf-8")
        if set(inventory) != {"schema_version", "artifact", "generator", "modules"} or type(inventory.get("schema_version")) is not int or inventory["schema_version"] != 1:
            raise PackageError("inventory_invalid")
        artifact = inventory["artifact"]
        if (not isinstance(artifact, dict) or set(artifact) != {"goos", "goarch", "cgo_enabled", "package"}
                or artifact != {"goos": "linux", "goarch": "amd64", "cgo_enabled": False,
                                "package": "happyranch/linux-tsnet-sidecar"}
                or inventory["generator"] != "tools/generate_inventory.py"):
            raise PackageError("inventory_invalid")
        modules = inventory["modules"]
        if not isinstance(modules, list) or not modules:
            raise PackageError("inventory_invalid")
    except (UnicodeDecodeError, KeyError, TypeError) as exc:
        raise PackageError("notice_invalid") from exc
    blocks = text.split("\n---\n")
    seen: dict[str, tuple[str, str]] = {}
    for block in blocks:
        spdx = next((line.removeprefix("SPDX: ") for line in block.splitlines() if line.startswith("SPDX: ")), None)
        digest = next((line.removeprefix("License-SHA256: ") for line in block.splitlines() if line.startswith("License-SHA256: ")), None)
        lines = block.splitlines()
        try:
            fence = lines.index("```text")
            end = lines.index("```", fence + 1)
            license_text = "\n".join(lines[fence + 1:end]).rstrip() + "\n"
        except ValueError:
            license_text = ""
        if not digest or not license_text or _sha(license_text.encode()) != digest:
            if any(line.startswith("- ") for line in lines):
                raise PackageError("notice_invalid")
        for line in lines:
            if line.startswith("- ") and "@" in line:
                coordinate = line[2:].strip()
                if coordinate in seen or not spdx or not digest:
                    raise PackageError("notice_invalid")
                seen[coordinate] = (spdx, digest)
    required = ("module", "version", "sum", "source", "spdx", "license_sha256", "relationship")
    if any(not isinstance(item, dict) or set(item) != set(required)
           or any(type(item.get(key)) is not str or not item[key] for key in required)
           or item["source"] != f"https://{item['module']}"
           or item["relationship"] != "statically-linked-linux-build-input"
           or not item["sum"].startswith("h1:")
           or len(item["license_sha256"]) != 64 for item in modules):
        raise PackageError("inventory_invalid")
    expected = {f"{item['module']}@{item['version']}": (item["spdx"], item["license_sha256"]) for item in modules}
    if len(expected) != len(modules):
        raise PackageError("inventory_invalid")
    if seen != expected:
        raise PackageError("notice_inventory_mismatch")


def _read_verified(package: Path) -> tuple[dict[str, bytes], dict[str, object]]:
    _require_package_source(package)
    files: dict[str, bytes] = {}
    modes: dict[str, int] = {}
    with tarfile.open(package) as archive:
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            if not member.isfile() or path.is_absolute() or not path.parts or ".." in path.parts or path.parts[0] != PREFIX:
                raise PackageError("archive_member_invalid")
            relative = str(path.relative_to(PREFIX))
            if relative in files:
                raise PackageError("archive_duplicate_member")
            if member.uid != 0 or member.gid != 0 or member.uname != "root" or member.gname != "root":
                raise PackageError("archive_owner_invalid")
            files[relative] = archive.extractfile(member).read()
            modes[relative] = member.mode & 0o7777
    try:
        manifest = json.loads(files["manifest.json"], object_pairs_hook=_reject_duplicate_keys)
        contract = manifest.get("managed_lifecycle_contract")
        if (not isinstance(contract, dict) or contract != MANAGED_LIFECYCLE_CONTRACT
                or any(type(value) is not int for value in contract.values())):
            raise PackageError("managed_lifecycle_incompatible")
        if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1 or manifest["architecture"] != "linux-amd64" or type(manifest["version"]) is not str or not manifest["version"]:
            raise PackageError("manifest_invalid")
        entries = manifest["files"]
        if not isinstance(entries, list) or type(manifest["sidecar_dependency_count"]) is not int:
            raise PackageError("manifest_invalid")
        expected_paths = set(files) - {"manifest.json"}
        declared_paths = {item["path"] for item in entries}
        if len(entries) != len(declared_paths) or declared_paths != expected_paths:
            raise PackageError("manifest_membership_mismatch")
        if declared_paths != set(PAYLOAD_MODES):
            raise PackageError("manifest_path_invalid")
        expected_modes = {name: int(mode, 8) for name, mode in PAYLOAD_MODES.items()}
        expected_modes["manifest.json"] = 0o644
        if any(modes[name] != mode for name, mode in expected_modes.items()):
            raise PackageError("archive_mode_invalid")
        for item in entries:
            if not isinstance(item, dict) or set(item) != {"path", "sha256", "mode"} or any(type(item[k]) is not str for k in item):
                raise PackageError("manifest_invalid")
            path = PurePosixPath(item["path"])
            if path.is_absolute() or ".." in path.parts or str(path) != item["path"] or item["path"] not in PAYLOAD_MODES:
                raise PackageError("manifest_path_invalid")
            if item["mode"] != PAYLOAD_MODES[item["path"]]:
                raise PackageError("manifest_mode_invalid")
            if _sha(files[item["path"]]) != item["sha256"]:
                raise PackageError("manifest_hash_mismatch")
        inventory = json.loads(files["share/dependency-inventory.json"])
        if manifest["sidecar_dependency_count"] != len(inventory["modules"]):
            raise PackageError("manifest_count_invalid")
        sbom = json.loads(files["share/sbom.cdx.json"])
        if not isinstance(sbom, dict):
            raise PackageError("sbom_invalid")
        if (type(sbom.get("version")) is not int or sbom.get("bomFormat") != "CycloneDX"
                or sbom.get("specVersion") != "1.5" or not isinstance(sbom.get("components"), list)):
            raise PackageError("sbom_invalid")
        def component_tuple(c: object) -> tuple[object, ...]:
            if not isinstance(c, dict): raise PackageError("sbom_invalid")
            props = c.get("properties")
            licenses = c.get("licenses")
            if not isinstance(props, list) or not isinstance(licenses, list) or len(licenses) != 1:
                raise PackageError("sbom_invalid")
            prop_map = {p.get("name"): p.get("value") for p in props if isinstance(p, dict)}
            try: spdx = licenses[0]["license"]["id"]
            except (KeyError, TypeError): raise PackageError("sbom_invalid")
            return (c.get("name"), c.get("version"), c.get("purl"), spdx,
                    prop_map.get("happyranch:go.sum"), prop_map.get("happyranch:license-sha256"))
        components = {component_tuple(c) for c in sbom["components"]}
        inventory_coordinates = {(m["module"], m["version"], f"pkg:golang/{m['module']}@{m['version']}",
                                  m["spdx"], m["sum"], m["license_sha256"]) for m in inventory["modules"]}
        if len(components) != len(sbom["components"]) or components != inventory_coordinates:
            raise PackageError("sbom_inventory_mismatch")
        _validate_evidence(inventory, files["share/THIRD_PARTY_NOTICES.md"])
    except PackageError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise PackageError("manifest_invalid") from exc
    return files, manifest


def _require_package_operand(path: Path, *, directory: bool | None = None,
                             link_object: bool = False) -> None:
    """Admit local package operands without granting managed authority.

    OLD tree links may be inventoried or unlinked as objects, never opened.
    All content operands require canonical parents and single-link regular
    custody. The literal filesystem root is a directory check only; its
    managed execution admission remains in the existing root-aware callers.
    """
    path = Path(path)
    if ".." in path.parts:
        raise PackageError("managed_lifecycle_owner_unknown")
    _assert_safe_root_ancestry(path)
    if path == Path("/"):
        if directory is False:
            raise PackageError("managed_lifecycle_owner_unknown")
        return
    if link_object:
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise PackageError("managed_lifecycle_owner_unknown") from exc
        else:
            if stat.S_ISLNK(metadata.st_mode):
                # Check the containing namespace, not the link's target.
                _require_package_operand(path.parent, directory=True)
                return
    _require_nonmanaged_state_owner(path, staging=True, directory=directory)


def _require_package_source(package: Path) -> None:
    # The existing private handoff deliberately uses immutable anonymous
    # memfds. Admit that exact descriptor form independently of path aliases;
    # ordinary files (including protected state) cannot meet these seals.
    if (package.parent == Path("/proc/self/fd")
            and re.fullmatch(r"[0-9]+", package.name) is not None):
        try:
            descriptor = int(package.name)
            metadata = os.fstat(descriptor)
            _add_seals, get_seals, required, _flags = _package_sealing_constants()
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 0
                    or fcntl.fcntl(descriptor, get_seals) & required != required):
                raise PackageError("package_input_invalid")
        except OSError as exc:
            raise PackageError("package_input_invalid") from exc
        return
    _require_package_operand(package, directory=False)


def _managed_package_root(root: Path) -> bool:
    """Admit only literal managed / or positively separate staging ownership.

    Resolution is exclusion evidence only. Never turn an alias into a managed
    invocation or permission to traverse links. Check the finite package
    destinations, including existing inode aliases, before any preparation.
    """
    if root == Path("/"):
        return True
    if ".." in root.parts:
        raise PackageError("managed_package_context_required")
    _assert_safe_root_ancestry(root)
    destinations = (root, root / "opt/happyranch", root / "etc/systemd/system",
                    *(root / "etc/systemd/system" / name for name in UNITS),
                    root / "etc/systemd/system" / _DROPIN_SERVICE_DIR / _DROPIN_FILE_NAME,
                    *_transaction_paths(root), _record_temp(root))
    protected = (Path("/opt/happyranch"), Path("/opt/happyranch/bin"),
                 Path("/opt/happyranch/share"), Path("/etc/systemd/system"),
                 Path("/var/lib/happyranch-connector"), Path("/var/lib/happyranch-tsnet-sidecar"),
                 *(Path("/etc/systemd/system") / name for name in UNITS),
                 Path("/etc/systemd/system") / _DROPIN_SERVICE_DIR / _DROPIN_FILE_NAME,
                 *_transaction_paths(Path("/")), _record_temp(Path("/")))
    for selected in destinations:
        _require_package_operand(selected)
        destination = _nonmanaged_owner_destination(selected)
        if destination != Path(os.path.abspath(selected)):
            raise PackageError("managed_package_context_required")
        for fixed in protected:
            # Ancestors catch directory aliases even when a fixed leaf is
            # absent; individual leaves also catch existing hard-link aliases.
            if (any(_nonmanaged_owner_same_path(parent, fixed)
                    for parent in (destination, *destination.parents))
                    or any(_nonmanaged_owner_same_path(destination, parent)
                           for parent in fixed.parents)):
                raise PackageError("managed_package_context_required")
            left, right = destination, fixed
            while left.name == right.name and left.parent != left and right.parent != right:
                left, right = left.parent, right.parent
                if _nonmanaged_owner_same_path(left, right):
                    raise PackageError("managed_package_context_required")
    return False


def _transaction_paths(root: Path) -> tuple[Path, Path, Path]:
    return root / _PAYLOAD_BACKUP_NAME, root / _UNIT_BACKUP_NAME, root / TRANSACTION_MARKER


def _record_temp(root: Path) -> Path:
    return root / (TRANSACTION_MARKER + ".tmp")


def _seam(guard, stage: str, operation: str, path: Path) -> None:
    """Invoke the narrow injectable filesystem seam.

    ``guard`` (when supplied) is called immediately before (``stage="before"``)
    and after (``stage="after"``) every real filesystem mutation this module
    performs, with the exact normalized operation name and target path.  A
    guard may raise to simulate a fault.  Occurrence scoping, one-shot
    disarmament, hit receipts and partial effects are the guard's
    responsibility: a ``partial`` guard performs a real truncated mutation at
    the target and then raises, exactly like a torn write.
    """
    if _MANAGED_PACKAGE.get():
        _check_managed_holder()
    if guard is not None:
        guard(stage, operation, str(path))


def _unlink(path: Path, guard, operation: str) -> None:
    _require_package_operand(path, link_object=True)
    _seam(guard, "before", operation, path)
    os.unlink(path)
    _seam(guard, "after", operation, path)


def _write_file(path: Path, raw: bytes, mode: int, guard, operation: str) -> None:
    _require_package_operand(path, directory=False)
    _seam(guard, "before", operation, path)
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        mode,
    )
    try:
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
    finally:
        os.close(descriptor)
    _seam(guard, "after", operation, path)
    _require_package_operand(path, directory=False)
    _seam(guard, "before", f"{operation}:chmod", path)
    os.chmod(path, mode)
    _seam(guard, "after", f"{operation}:chmod", path)


def _copy_file(source: Path, destination: Path, guard, operation: str) -> None:
    _require_package_operand(source, directory=False)
    _require_package_operand(destination, directory=False)
    _seam(guard, "before", operation, destination)
    shutil.copy2(source, destination)
    _seam(guard, "after", operation, destination)


def _backup_inventory(path: Path) -> dict:
    _require_package_operand(path, directory=False)
    return {"sha256": _sha(path.read_bytes()), "mode": stat.S_IMODE(path.lstat().st_mode)}


def _backup_intact(path: Path, expected: object) -> bool:
    _require_package_operand(path, directory=False)
    if expected is None:
        return not (path.exists() or path.is_symlink())
    if not isinstance(expected, dict) or not path.is_file() or path.is_symlink():
        return False
    try:
        metadata = path.lstat()
        return (
            stat.S_IMODE(metadata.st_mode) == expected["mode"]
            and _sha(path.read_bytes()) == expected["sha256"]
        )
    except OSError:
        return False


def _backup_present(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _path_file_identity(path: Path) -> dict:
    _require_package_operand(path, directory=False)
    return {"sha256": _sha(path.read_bytes()), "mode": stat.S_IMODE(path.lstat().st_mode)}


def _planned_payload_inventory(files: Mapping[str, bytes], system_service: bool) -> dict:
    """Deterministic identity of the payload tree the stage will publish."""
    root_mode = 0o755 if system_service else 0o700
    entries: dict[str, dict] = {
        "bin": {"type": "dir", "mode": 0o755 if system_service else 0o700},
        "share": {"type": "dir", "mode": 0o755 if system_service else 0o700},
    }
    for name, raw in files.items():
        if name.startswith("systemd/"):
            continue
        mode = (0o644 if system_service else 0o600) if name == "manifest.json" else (
            int(PAYLOAD_MODES[name], 8) if system_service
            else (0o700 if name.startswith("bin/") else 0o600)
        )
        entries[name] = {"type": "file", "mode": mode, "sha256": _sha(raw)}
    return {"root_mode": root_mode, "entries": entries}


def _inventory_tree(path: Path, *, metadata_only: bool = False) -> dict:
    """Inventory without following links; preflight alone reads no contents.

    The default exact byte/mode fingerprint remains the schema2 identity.
    Metadata-only results are used solely for operand admission, never stored
    or compared as a transaction identity.
    """
    _require_package_operand(path, directory=True)
    root_mode = stat.S_IMODE(path.lstat().st_mode)
    entries: dict[str, dict] = {}
    stack: list[tuple[Path, str]] = [(path, "")]
    while stack:
        base, prefix = stack.pop()
        for entry in sorted(base.iterdir(), key=lambda item: item.name):
            _require_package_operand(entry, link_object=True)
            relative = f"{prefix}{entry.name}"
            metadata = entry.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                entries[relative] = {"type": "link", "target": os.readlink(entry)}
            elif stat.S_ISDIR(metadata.st_mode):
                entries[relative] = {"type": "dir", "mode": stat.S_IMODE(metadata.st_mode)}
                stack.append((entry, f"{relative}/"))
            elif stat.S_ISREG(metadata.st_mode):
                entries[relative] = {
                    "type": "file", "mode": stat.S_IMODE(metadata.st_mode),
                    **({"sha256": _sha(entry.read_bytes())} if not metadata_only else {}),
                }
            else:
                entries[relative] = {"type": "other"}
    return {"root_mode": root_mode, "entries": entries}


def _tree_matches(path: Path, inventory: object) -> bool:
    if not isinstance(inventory, dict):
        return False
    try:
        metadata = path.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            return False
        if stat.S_IMODE(metadata.st_mode) != inventory.get("root_mode"):
            return False
        return _inventory_tree(path) == inventory
    except OSError:
        return False


def _tree_is_owned_partial(path: Path, inventory: object) -> bool:
    """True when every surviving entry is an exact member of the recorded tree.

    A rollback that removes a freshly published payload can be interrupted
    after any interior unlink/rmdir.  The remainder is then a strict subset of
    the recorded NEW identity: no entry may exist outside that inventory and
    every surviving entry must match its recorded type/mode/bytes.  Foreign,
    corrupt or unexplained content therefore still refuses.
    """
    if not isinstance(inventory, dict) or set(inventory) != {"root_mode", "entries"}:
        return False
    entries = inventory.get("entries")
    if not isinstance(entries, dict):
        return False
    try:
        metadata = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        return False
    if stat.S_IMODE(metadata.st_mode) != inventory.get("root_mode"):
        return False
    try:
        present = _inventory_tree(path)["entries"]
    except OSError:
        return False
    if not set(present).issubset(entries):
        return False
    return all(entry == entries[relative] for relative, entry in present.items())


def _file_matches(path: Path, identity: object) -> bool:
    _require_package_operand(path, directory=False)
    if not isinstance(identity, dict):
        return False
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            return False
        return (
            stat.S_IMODE(metadata.st_mode) == identity.get("mode")
            and _sha(path.read_bytes()) == identity.get("sha256")
        )
    except OSError:
        return False


def _valid_file_identity(identity: object) -> bool:
    return (
        isinstance(identity, dict) and set(identity) == {"sha256", "mode"}
        and isinstance(identity.get("sha256"), str) and len(identity["sha256"]) == 64
        and type(identity.get("mode")) is int
    )


def _valid_inventory(inventory: object) -> bool:
    if not isinstance(inventory, dict) or set(inventory) != {"root_mode", "entries"}:
        return False
    if type(inventory.get("root_mode")) is not int:
        return False
    entries = inventory.get("entries")
    if not isinstance(entries, dict):
        return False
    for key, value in entries.items():
        if (not isinstance(key, str) or not key or key.startswith("/")
                or ".." in PurePosixPath(key).parts or not isinstance(value, dict)):
            return False
        kind = value.get("type")
        if kind == "dir":
            if set(value) != {"type", "mode"} or type(value.get("mode")) is not int:
                return False
        elif kind == "file":
            if (set(value) != {"type", "mode", "sha256"}
                    or type(value.get("mode")) is not int
                    or not isinstance(value.get("sha256"), str)
                    or len(value["sha256"]) != 64):
                return False
        elif kind == "link":
            if set(value) != {"type", "target"} or not isinstance(value.get("target"), str):
                return False
        elif kind == "other":
            if set(value) != {"type"}:
                return False
        else:
            return False
    return True


def _assert_safe_root_ancestry(root: Path) -> None:
    """Refuse a selected root reached through a symlinked ancestor.

    ``_assert_safe_target`` only inspects the root itself and the components
    *below* it.  A symlink anywhere above the selected root silently redirects
    every subsequent write outside the caller's chosen tree, so the whole
    ancestor chain is checked once per entry point without following links.
    Components that do not exist yet are tolerated (the leaf checks cover
    them); an existing component must be a real directory.
    """
    target = Path(os.path.abspath(root))
    current = Path(target.anchor)
    for part in target.parts[1:-1]:
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise PackageError("transaction_state_invalid") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise PackageError("transaction_state_invalid")


def _assert_safe_target(root: Path, path: Path, *, directory: bool | None = None) -> None:
    """Reject unsafe types/symlinks on the exact path or any ancestor below root.

    ``directory`` narrows the expected leaf type: ``True`` requires a real
    directory, ``False`` a regular file, and ``None`` accepts either while
    still refusing links, FIFOs, sockets and devices.  Type is validated here,
    before any filesystem operation, so an unexpected leaf never reaches
    ``shutil`` as an unrelated exception category.
    """
    root, path = Path(root), Path(path)
    _require_package_operand(root, directory=True)
    _require_package_operand(path, directory=directory)
    try:
        metadata = root.lstat()
    except OSError as exc:
        raise PackageError("transaction_state_invalid") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise PackageError("transaction_state_invalid")
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise PackageError("transaction_state_invalid") from exc
    current = root
    parts = relative.parts
    for index, part in enumerate(parts):
        current = current / part
        try:
            child = current.lstat()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise PackageError("transaction_state_invalid") from exc
        if stat.S_ISLNK(child.st_mode):
            raise PackageError("transaction_state_invalid")
        if index != len(parts) - 1:
            if not stat.S_ISDIR(child.st_mode):
                raise PackageError("transaction_state_invalid")
        elif directory is True:
            if not stat.S_ISDIR(child.st_mode):
                raise PackageError("transaction_state_invalid")
        elif directory is False:
            if not stat.S_ISREG(child.st_mode):
                raise PackageError("transaction_state_invalid")
        elif not (stat.S_ISDIR(child.st_mode) or stat.S_ISREG(child.st_mode)):
            raise PackageError("transaction_state_invalid")


def _record_paths(root: Path, record: Mapping[str, object]) -> list[tuple[Path, bool]]:
    payload_backup, unit_backup, marker = _transaction_paths(root)
    paths: list[tuple[Path, bool]] = [
        (root, True), (marker, False), (_record_temp(root), False),
        (payload_backup, True), (unit_backup, True),
        (root / "opt", True), (root / "opt/happyranch", True),
        (root / "etc/systemd/system", True),
        (root / "etc/systemd/system" / _DROPIN_SERVICE_DIR, True),
        (root / "etc/systemd/system" / _DROPIN_SERVICE_DIR / _DROPIN_FILE_NAME, False),
    ]
    paths.extend((root / "etc/systemd/system" / unit, False) for unit in UNITS)
    paths.extend((unit_backup / unit, False) for unit in UNITS)
    paths.extend([
        (unit_backup / _DROPIN_SERVICE_DIR, True),
        (unit_backup / _DROPIN_BACKUP_RELATIVE, False),
    ])
    stage = record.get("stage")
    if stage is not None:
        paths.append((Path(str(stage)), True))
    return paths


def _assert_record_paths_safe(root: Path, record: Mapping[str, object]) -> None:
    for path, directory in _record_paths(root, record):
        _assert_safe_target(root, path, directory=directory)


def _replace(source: Path, destination: Path, guard, operation: str) -> None:
    _require_package_operand(source)
    _require_package_operand(destination)
    for path in (source, destination):
        if path.is_dir():
            _inventory_tree(path, metadata_only=True)
    _seam(guard, "before", operation, destination)
    os.replace(source, destination)
    _seam(guard, "after", operation, destination)


def _ensure_dir(path: Path, mode: int, guard, operation: str) -> bool:
    _require_package_operand(path, directory=True)
    if path.is_symlink():
        raise PackageError("transaction_state_invalid")
    if path.exists():
        if not path.is_dir():
            raise PackageError("transaction_state_invalid")
        return False
    _seam(guard, "before", f"{operation}:mkdir", path)
    path.mkdir()
    _seam(guard, "after", f"{operation}:mkdir", path)
    _seam(guard, "before", f"{operation}:chmod", path)
    path.chmod(mode)
    _seam(guard, "after", f"{operation}:chmod", path)
    return True


def _remove_tree(path: Path, guard, operation: str) -> None:
    """Recursively remove an owned tree using only exact, no-follow operations."""
    _require_package_operand(path, link_object=True)
    if not path.is_symlink() and path.is_dir():
        # Reuse the existing inventory walk before the first deletion, even
        # for a direct lower call. Unknown later descendants retain the tree.
        _inventory_tree(path, metadata_only=True)

    def remove(selected: Path) -> None:
        _require_package_operand(selected, link_object=True)
        if selected.is_symlink() or selected.is_file():
            _unlink(selected, guard, f"{operation}:unlink")
            return
        for entry in sorted(selected.iterdir()):
            remove(entry)
        _seam(guard, "before", f"{operation}:rmdir", selected)
        selected.rmdir()
        _seam(guard, "after", f"{operation}:rmdir", selected)

    remove(path)


def _write_record(root: Path, record: dict, guard) -> None:
    """Atomically publish the transaction record, preserving the prior record."""
    if _managed_package_root(root):
        _require_managed_package_context()
    _assert_record_paths_safe(root, record)
    marker = root / TRANSACTION_MARKER
    temporary = _record_temp(root)
    raw = (json.dumps(record, sort_keys=True) + "\n").encode("utf-8")
    _seam(guard, "before", "record_temp_create", temporary)
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        _seam(guard, "after", "record_temp_create", temporary)
        _seam(guard, "before", "record_temp_write", temporary)
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        _seam(guard, "after", "record_temp_write", temporary)
        _seam(guard, "before", "record_temp_fsync", temporary)
        os.fsync(descriptor)
        _seam(guard, "after", "record_temp_fsync", temporary)
    finally:
        os.close(descriptor)
    _require_package_operand(temporary, directory=False)
    _seam(guard, "before", "record_temp_chmod", temporary)
    os.chmod(temporary, 0o600)
    _seam(guard, "after", "record_temp_chmod", temporary)
    _require_package_operand(temporary, directory=False)
    _require_package_operand(marker, directory=False)
    _seam(guard, "before", "record_replace", marker)
    os.replace(temporary, marker)
    _seam(guard, "after", "record_replace", marker)


def _publish(root: Path, record: Mapping[str, object], guard, **changes: object) -> dict:
    """Durably publish an updated record; the caller keeps the prior record on failure.

    The returned mapping is the last successfully *published* authority, so an
    exception handler never acts on an in-memory state whose record update did
    not reach disk.
    """
    updated = {**record, **changes}
    _write_record(root, updated, guard)
    return updated


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise PackageError("transaction_state_invalid")
        result[key] = value
    return result


def _allowed_created_parents(record: Mapping[str, object]) -> set[str]:
    """The closed, source-derived universe of parents an install may create."""
    allowed = set(_CREATED_PARENT_PLAN)
    if record.get("new_dropin") is not None:
        allowed.add(f"etc/systemd/system/{_DROPIN_SERVICE_DIR}")
    return allowed


def _backups_complete(record: Mapping[str, object]) -> bool:
    """True when recorded prior-existence matches the recorded backup inventory."""
    backups = record["backups"]
    if not isinstance(backups, dict):
        return False
    if (backups["payload"] is not None) != record["payload_present"]:
        return False
    if (backups["dropin"] is not None) != record["dropin_present"]:
        return False
    return all(
        (backups["units"][unit] is not None) == record["units"][unit] for unit in UNITS
    )


def _validate_progress(record: Mapping[str, object]) -> None:
    """Reject phase/progress/prior-existence combinations that cannot occur.

    Every accepted phase is reachable only through the source publication
    sequence in ``install_linux_package``; a marker whose phase and recorded
    progress contradict that sequence (for example a ``committed`` record that
    never published any unit) is refused before any cleanup could act on it.
    """
    phase = record["phase"]
    published = record["published_units"]
    if phase == "preparing":
        empty_units = {unit: None for unit in UNITS}
        if record["backups"] != {"payload": None, "units": empty_units, "dropin": None}:
            raise PackageError("transaction_state_invalid")
        if published or record["dropin_published"]:
            raise PackageError("transaction_state_invalid")
        return
    if phase == "rolling_back":
        # A rollback may have consumed any subset of the recorded backups;
        # per-path identity classification already refuses unexplained gaps.
        return
    if not _backups_complete(record):
        raise PackageError("transaction_state_invalid")
    if phase in {"prepared", "payload_retained", "payload_published"}:
        if published or record["dropin_published"]:
            raise PackageError("transaction_state_invalid")
    elif phase == "units_publishing":
        if record["dropin_published"]:
            raise PackageError("transaction_state_invalid")
    elif phase == "dropin_publishing":
        if set(published) != set(UNITS):
            raise PackageError("transaction_state_invalid")
        if record["dropin_published"] and record["new_dropin"] is None:
            raise PackageError("transaction_state_invalid")
    elif phase == "committed":
        if set(published) != set(UNITS):
            raise PackageError("transaction_state_invalid")
        if record["dropin_published"] != (record["new_dropin"] is not None):
            raise PackageError("transaction_state_invalid")


def _load_record(root: Path, marker: Path) -> dict:
    """Strictly classify an existing record; any ambiguity is refused unchanged."""
    _assert_safe_target(root, marker, directory=False)
    try:
        record = json.loads(
            marker.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except PackageError:
        raise
    except (OSError, ValueError) as exc:
        raise PackageError("transaction_state_invalid") from exc
    if not isinstance(record, dict) or set(record) != _TRANSACTION_KEYS:
        raise PackageError("transaction_state_invalid")
    if type(record["schema_version"]) is not int or record["schema_version"] != TRANSACTION_SCHEMA_VERSION:
        raise PackageError("transaction_state_invalid")
    attempt = record["attempt_id"]
    if not isinstance(attempt, str) or _ATTEMPT_ID_PATTERN.fullmatch(attempt) is None:
        raise PackageError("transaction_state_invalid")
    if record["root"] != str(root):
        raise PackageError("transaction_state_invalid")
    if not isinstance(record["phase"], str) or record["phase"] not in _TRANSACTION_PHASES:
        raise PackageError("transaction_state_invalid")
    if any(type(record[key]) is not bool for key in ("payload_present", "dropin_present", "dropin_published")):
        raise PackageError("transaction_state_invalid")
    units = record["units"]
    if not isinstance(units, dict) or set(units) != set(UNITS) or any(type(value) is not bool for value in units.values()):
        raise PackageError("transaction_state_invalid")
    created = record["created_parents"]
    if not isinstance(created, list) or any(not isinstance(item, str) for item in created):
        raise PackageError("transaction_state_invalid")
    if len(set(created)) != len(created):
        raise PackageError("transaction_state_invalid")
    if any(
        not item or item.startswith("/")
        or ".." in PurePosixPath(item).parts
        for item in created
    ):
        raise PackageError("transaction_state_invalid")
    if any(item not in _allowed_created_parents(record) for item in created):
        raise PackageError("transaction_state_invalid")
    published = record["published_units"]
    if not isinstance(published, list) or any(not isinstance(unit, str) for unit in published):
        raise PackageError("transaction_state_invalid")
    if len(set(published)) != len(published):
        raise PackageError("transaction_state_invalid")
    if any(unit not in UNITS for unit in published):
        raise PackageError("transaction_state_invalid")
    # The writer always records the concrete allocated stage before any
    # mutation.  A missing/null stage identity is an unreachable, incomplete
    # record: accepting it would let recovery discard the marker while leaving
    # the real owned stage orphaned.  The recorded string may legitimately name
    # a stage whose path has already moved (payload publication renames it into
    # place), so only the identity is validated here, never the path's presence.
    stage = record["stage"]
    if not isinstance(stage, str) or not stage:
        raise PackageError("transaction_state_invalid")
    stage_path = Path(stage)
    if stage != str(stage_path) or stage_path.parent != Path(root):
        raise PackageError("transaction_state_invalid")
    prefix = f"{_STAGE_PREFIX}{attempt}-"
    if not stage_path.name.startswith(prefix):
        raise PackageError("transaction_state_invalid")
    if _STAGE_SUFFIX_PATTERN.fullmatch(stage_path.name[len(prefix):]) is None:
        raise PackageError("transaction_state_invalid")
    if not _valid_inventory(record["new_payload"]):
        raise PackageError("transaction_state_invalid")
    new_units = record["new_units"]
    if not isinstance(new_units, dict) or set(new_units) != set(UNITS):
        raise PackageError("transaction_state_invalid")
    if any(not _valid_file_identity(identity) for identity in new_units.values()):
        raise PackageError("transaction_state_invalid")
    if record["new_dropin"] is not None and not _valid_file_identity(record["new_dropin"]):
        raise PackageError("transaction_state_invalid")
    backups = record["backups"]
    if not isinstance(backups, dict) or set(backups) != {"payload", "units", "dropin"}:
        raise PackageError("transaction_state_invalid")
    if backups["payload"] is not None and not _valid_inventory(backups["payload"]):
        raise PackageError("transaction_state_invalid")
    backup_units = backups["units"]
    if not isinstance(backup_units, dict) or set(backup_units) != set(UNITS):
        raise PackageError("transaction_state_invalid")
    for entry in [*backup_units.values(), backups["dropin"]]:
        if entry is None:
            continue
        if not _valid_file_identity(entry):
            raise PackageError("transaction_state_invalid")
    _validate_progress(record)
    return record


def _planned_created_parents(root: Path, *, include_dropin_dir: bool) -> list[str]:
    candidates = [root / "opt", root / "etc", root / "etc/systemd", root / "etc/systemd/system"]
    if include_dropin_dir:
        candidates.append(root / "etc/systemd/system" / _DROPIN_SERVICE_DIR)
    return [str(path.relative_to(root)) for path in candidates if not path.exists()]


def _remove_created_parents(root: Path, record: dict, guard) -> None:
    if _managed_package_root(root):
        _require_managed_package_context()
    for relative in sorted(record["created_parents"], key=lambda item: len(PurePosixPath(item).parts), reverse=True):
        path = root / relative
        _assert_safe_target(root, path, directory=True)
        if path.is_dir() and not path.is_symlink() and not any(path.iterdir()):
            _seam(guard, "before", "created_parent_remove", path)
            path.rmdir()
            _seam(guard, "after", "created_parent_remove", path)


def _cleanup_owned(root: Path, record: dict, guard) -> None:
    """Remove exact recorded owned residue, then the marker last."""
    managed = _managed_package_root(root)
    if managed:
        _require_managed_package_context()
        _package_managed_history(_MANAGED_DEADLINE.get())
    _assert_record_paths_safe(root, record)
    payload_backup, unit_backup, marker = _transaction_paths(root)
    trees = [payload_backup, unit_backup]
    if record.get("stage") is not None:
        trees.append(Path(record["stage"]))
    for path in trees:
        if _backup_present(path):
            _inventory_tree(path, metadata_only=True)
    for path in (payload_backup, unit_backup):
        _assert_safe_target(root, path, directory=True)
        if _backup_present(path):
            _remove_tree(path, guard, "backup_remove")
    temporary = _record_temp(root)
    _assert_safe_target(root, temporary, directory=False)
    if _backup_present(temporary):
        _unlink(temporary, guard, "record_temp_remove")
    stage = record.get("stage")
    if stage is not None:
        stage_path = Path(stage)
        _assert_safe_target(root, stage_path, directory=True)
        if _backup_present(stage_path):
            _remove_tree(stage_path, guard, "stage_remove")
    _remove_created_parents(root, record, guard)
    _assert_safe_target(root, marker, directory=False)
    if _backup_present(marker):
        _unlink(marker, guard, "marker_remove")


def _classify_payload(root: Path, record: dict) -> str:
    opt = root / "opt/happyranch"
    payload_backup, _unit_backup, _marker = _transaction_paths(root)
    old_payload = record["backups"]["payload"]
    if record["payload_present"]:
        if old_payload is None:
            raise PackageError("transaction_state_invalid")
        if _backup_present(payload_backup):
            if not _tree_matches(payload_backup, old_payload):
                raise PackageError("transaction_state_invalid")
            return "already" if _tree_matches(opt, old_payload) else "restore"
        if _tree_matches(opt, old_payload):
            return "already"
        raise PackageError("transaction_state_invalid")
    if old_payload is not None or _backup_present(payload_backup):
        raise PackageError("transaction_state_invalid")
    if opt.is_symlink():
        raise PackageError("transaction_state_invalid")
    if not opt.exists():
        return "nothing"
    if _tree_matches(opt, record["new_payload"]):
        return "remove"
    # A fresh rollback publishes ``rolling_back`` before it unlinks any member,
    # so a partial NEW tree that is a strict subset of the recorded NEW
    # inventory is genuine resumable progress rather than foreign content.
    if record["phase"] == "rolling_back" and _tree_is_owned_partial(opt, record["new_payload"]):
        return "remove"
    raise PackageError("transaction_state_invalid")


def _classify_units(root: Path, record: dict) -> dict[str, str]:
    units = root / "etc/systemd/system"
    _payload_backup, unit_backup, _marker = _transaction_paths(root)
    actions: dict[str, str] = {}
    for unit in UNITS:
        target = units / unit
        saved = unit_backup / unit
        expected = record["backups"]["units"][unit]
        if record["units"][unit]:
            if expected is None:
                raise PackageError("transaction_state_invalid")
            if _backup_present(saved):
                if not _backup_intact(saved, expected):
                    raise PackageError("transaction_state_invalid")
                actions[unit] = "already" if _file_matches(target, expected) else "restore"
            elif _file_matches(target, expected):
                actions[unit] = "already"
            else:
                raise PackageError("transaction_state_invalid")
        else:
            if expected is not None:
                raise PackageError("transaction_state_invalid")
            if target.is_symlink():
                raise PackageError("transaction_state_invalid")
            if not target.exists():
                actions[unit] = "nothing"
            elif unit in record["published_units"]:
                actions[unit] = "remove"
            else:
                raise PackageError("transaction_state_invalid")
    return actions


def _classify_dropin(root: Path, record: dict) -> str:
    units = root / "etc/systemd/system"
    dropin = units / _DROPIN_SERVICE_DIR / _DROPIN_FILE_NAME
    _payload_backup, unit_backup, _marker = _transaction_paths(root)
    dropin_backup = unit_backup / _DROPIN_BACKUP_RELATIVE
    expected = record["backups"]["dropin"]
    if record["dropin_present"]:
        if expected is None:
            raise PackageError("transaction_state_invalid")
        if _backup_present(dropin_backup):
            if not _backup_intact(dropin_backup, expected):
                raise PackageError("transaction_state_invalid")
            return "already" if _file_matches(dropin, expected) else "restore"
        if _file_matches(dropin, expected):
            return "already"
        raise PackageError("transaction_state_invalid")
    if expected is not None or _backup_present(dropin_backup):
        raise PackageError("transaction_state_invalid")
    if dropin.is_symlink():
        raise PackageError("transaction_state_invalid")
    if not dropin.exists():
        return "nothing"
    if record["dropin_published"]:
        return "remove"
    raise PackageError("transaction_state_invalid")


def _assert_committed_active_new(root: Path, record: Mapping[str, object]) -> None:
    """Refuse a committed record whose active installation is not complete NEW.

    The committed phase is authoritative only when the active payload, every
    published unit and the applicable enrollment drop-in equal the exact
    identities the record intended to publish.  A contradictory record (for
    example a real ``units_publishing`` record whose phase was changed to
    ``committed`` while the final unit write never happened) must be refused
    *before* cleanup deletes the recorded OLD backups, so a mixed installation
    can never be silently promoted.  Commit validity is never inferred from
    the mere absence of backups.
    """
    opt = root / "opt/happyranch"
    if not _tree_matches(opt, record["new_payload"]):
        raise PackageError("transaction_state_invalid")
    units = root / "etc/systemd/system"
    new_units = record["new_units"]
    for unit in UNITS:
        if not _file_matches(units / unit, new_units[unit]):
            raise PackageError("transaction_state_invalid")
    dropin = units / _DROPIN_SERVICE_DIR / _DROPIN_FILE_NAME
    if record["new_dropin"] is not None:
        if not _file_matches(dropin, record["new_dropin"]):
            raise PackageError("transaction_state_invalid")
        return
    # No NEW drop-in was published: the unchanged-prior-drop-in branch must
    # still match its recorded prior identity, or be genuinely absent.
    if record["dropin_present"]:
        if not _file_matches(dropin, record["backups"]["dropin"]):
            raise PackageError("transaction_state_invalid")
    elif dropin.exists() or dropin.is_symlink():
        raise PackageError("transaction_state_invalid")


def _restore_old(root: Path, record: dict, guard) -> None:
    """Restore the last-known-good (OLD) composition or conservatively refuse.

    Every precondition (recorded backups intact, verified identity of any
    already-consumed backup, coherent payload/units/drop-in ownership) is
    classified BEFORE any mutation so that a refusal preserves all bytes and
    modes unchanged.  Restoring by atomic rename consumes a backup; the
    recorded OLD identity lets a later retry recognize the already-restored
    artifact instead of demanding the consumed backup again.
    """
    managed = _managed_package_root(root)
    if managed:
        _require_managed_package_context()
        _package_managed_history(_MANAGED_DEADLINE.get())
    opt = root / "opt/happyranch"
    units = root / "etc/systemd/system"
    dropin = units / _DROPIN_SERVICE_DIR / _DROPIN_FILE_NAME
    payload_backup, unit_backup, _marker = _transaction_paths(root)
    dropin_backup = unit_backup / _DROPIN_BACKUP_RELATIVE

    _assert_record_paths_safe(root, record)
    # Validate the actual tree that restore may remove before publishing any
    # rollback intent. The classifiers below still decide OLD/NEW ownership.
    trees = [opt, payload_backup, unit_backup]
    if record.get("stage") is not None:
        trees.append(Path(record["stage"]))
    for path in trees:
        if _backup_present(path):
            _inventory_tree(path, metadata_only=True)
    payload_action = _classify_payload(root, record)
    unit_actions = _classify_units(root, record)
    dropin_action = _classify_dropin(root, record)

    if record["phase"] != "rolling_back":
        record = _publish(root, record, guard, phase="rolling_back")

    if payload_action == "restore":
        if opt.exists() or opt.is_symlink():
            _remove_tree(opt, guard, "rollback_payload_remove")
        if managed:
            _require_managed_package_context()
        opt.parent.mkdir(parents=True, exist_ok=True)
        if managed:
            _require_managed_package_context()
        _replace(payload_backup, opt, guard, "rollback_payload_restore")
    elif payload_action == "remove":
        _remove_tree(opt, guard, "rollback_payload_remove")

    for unit in UNITS:
        action = unit_actions[unit]
        target = units / unit
        if action == "restore":
            if target.exists() or target.is_symlink():
                _unlink(target, guard, "rollback_unit_unlink")
            _replace(unit_backup / unit, target, guard, "rollback_unit_restore")
        elif action == "remove":
            if target.exists() or target.is_symlink():
                _unlink(target, guard, "rollback_unit_unlink")

    if dropin_action == "restore":
        if not dropin.parent.exists():
            if managed:
                _require_managed_package_context()
            dropin.parent.mkdir(parents=True, exist_ok=True)
            if managed:
                _require_managed_package_context()
        _replace(dropin_backup, dropin, guard, "rollback_dropin_restore")
    elif dropin_action == "remove":
        _unlink(dropin, guard, "rollback_dropin_unlink")

    _cleanup_owned(root, record, guard)


def _cleanup_pre_record(root: Path, stage: Path | None, guard) -> None:
    if _managed_package_root(root):
        _require_managed_package_context()
    temporary = _record_temp(root)
    _assert_safe_target(root, temporary, directory=False)
    if stage is not None:
        _assert_safe_target(root, Path(stage), directory=True)
        if _backup_present(Path(stage)):
            _inventory_tree(Path(stage), metadata_only=True)
    if _backup_present(temporary):
        _unlink(temporary, guard, "record_temp_remove")
    if stage is not None:
        stage_path = Path(stage)
        _assert_safe_target(root, stage_path, directory=True)
        if _backup_present(stage_path):
            _remove_tree(stage_path, guard, "stage_remove")


def _recover_interrupted(root: Path, guard=None) -> None:
    """Classify and recover an owned transaction, or refuse an ambiguous one.

    Conservative disposition: a legacy schema-v1 marker, an incomplete
    record, or unrecorded preparation residue is preserved unchanged and
    refused with the existing ``transaction_state_invalid`` category.  Genuine
    owned transactions are recovered to OLD (uncommitted) or completed NEW
    (committed).
    """
    if _managed_package_root(root):
        _require_managed_package_context()
        _installed_managed_contract(deadline_ns=_MANAGED_DEADLINE.get(), _package=True)
    _assert_safe_root_ancestry(root)
    if not root.exists() and not root.is_symlink():
        return
    if root.is_symlink() or not root.is_dir():
        raise PackageError("transaction_state_invalid")
    marker = root / TRANSACTION_MARKER
    _assert_safe_target(root, marker, directory=False)
    if not marker.exists():
        if marker.is_symlink():
            raise PackageError("transaction_state_invalid")
        if any(entry.name.startswith(_RESIDUE_PREFIXES) for entry in root.iterdir()):
            raise PackageError("transaction_state_invalid")
        return
    record = _load_record(root, marker)
    _assert_record_paths_safe(root, record)
    if record["phase"] == "committed":
        # ``committed`` is authoritative NEW only when the active installation
        # genuinely equals the recorded complete NEW identities; a
        # contradictory record is refused before cleanup can delete OLD.
        _assert_committed_active_new(root, record)
        _cleanup_owned(root, record, guard)
        return
    if record["phase"] == "preparing":
        # ``preparing`` never mutated OLD: recover by removing only exact
        # recorded owned residue.
        _cleanup_owned(root, record, guard)
        return
    _restore_old(root, record, guard)


def install_linux_package(
    package: Path,
    root: Path,
    *,
    system_service: bool = False,
    fault: Callable[[str], None] | None = None,
    guard=None,
) -> dict[str, object]:
    if type(system_service) is not bool:
        raise PackageError("install_mode_invalid")
    deadline = _managed_entry_deadline(180)
    managed = _managed_package_root(root)
    if system_service or managed:
        if not managed or not system_service or fault is not None or guard is not None:
            raise PackageError("managed_package_context_required")
        if not _MANAGED_PACKAGE.get():
            return _managed_package_outer(package, "package-install", deadline)
        _require_managed_package_context()
    if system_service:
        require_credential_capability(
            root / "etc/happyranch/daemon.token", expected_uid=os.geteuid()
        )
        enrollment_source = root / "etc/happyranch/enrollment.key"
        if enrollment_source.exists() or enrollment_source.is_symlink():
            require_credential_capability(
                enrollment_source, expected_uid=os.geteuid()
            )
    files, manifest = _read_verified(package)
    if system_service:
        _installed_managed_contract(deadline_ns=deadline, _package=True)
    _recover_interrupted(root, guard)
    if system_service:
        _installed_managed_contract(deadline_ns=deadline, _package=True)
    opt = root / "opt/happyranch"
    if _backup_present(opt):
        _inventory_tree(opt, metadata_only=True)
    _require_package_operand(root, directory=True)
    root.mkdir(parents=True, exist_ok=True)
    units = root / "etc/systemd/system"
    dropin_dir = units / _DROPIN_SERVICE_DIR
    dropin = dropin_dir / _DROPIN_FILE_NAME
    credential_source = root / "etc/happyranch/enrollment.key"
    publishes_dropin = system_service and credential_source.is_file()
    # Ownership/type preflight: no write or delete may follow a symlink or an
    # unexpected type at any target or ancestor below the selected root.
    typed_targets: list[tuple[Path, bool]] = [
        (root, True), (opt, True), (units, True), (dropin_dir, True), (dropin, False),
        (root / TRANSACTION_MARKER, False), (_record_temp(root), False),
        (root / _PAYLOAD_BACKUP_NAME, True), (root / _UNIT_BACKUP_NAME, True),
    ]
    typed_targets.extend((units / unit, False) for unit in UNITS)
    for path, directory in typed_targets:
        _assert_safe_target(root, path, directory=directory)
    payload_present = opt.exists()
    dropin_present = dropin.exists()
    unit_present = {name: (units / name).exists() for name in UNITS}
    created_parents = _planned_created_parents(root, include_dropin_dir=publishes_dropin)
    new_payload = _planned_payload_inventory(files, system_service)
    new_units = {
        unit: {"sha256": _sha(files[f"systemd/{unit}"]), "mode": 0o644 if system_service else 0o600} for unit in UNITS
    }
    new_dropin = {"sha256": _sha(_DROPIN_BYTES), "mode": 0o644} if publishes_dropin else None
    checkpoint = fault or (lambda _name: None)
    attempt_id = secrets.token_hex(16)
    stage: Path | None = None
    try:
        _seam(guard, "before", "stage_create", root)
        stage = Path(tempfile.mkdtemp(prefix=f"{_STAGE_PREFIX}{attempt_id}-", dir=root))
        _seam(guard, "after", "stage_create", stage)
        _seam(guard, "before", "stage_chmod", stage)
        stage.chmod(0o755 if system_service else 0o700)
        _seam(guard, "after", "stage_chmod", stage)
        record = {
            "schema_version": TRANSACTION_SCHEMA_VERSION,
            "attempt_id": attempt_id,
            "root": str(root),
            "phase": "preparing",
            "payload_present": payload_present,
            "units": dict(unit_present),
            "dropin_present": dropin_present,
            "stage": str(stage),
            "created_parents": created_parents,
            "published_units": [],
            "dropin_published": False,
            "backups": {"payload": None, "units": {name: None for name in UNITS}, "dropin": None},
            "new_payload": new_payload,
            "new_units": new_units,
            "new_dropin": new_dropin,
        }
        record = _publish(root, record, guard)

        for relative in ("opt", "etc", "etc/systemd", "etc/systemd/system"):
            _ensure_dir(root / relative, 0o755, guard, "parent")
        if publishes_dropin:
            _ensure_dir(dropin_dir, 0o755, guard, "parent")

        for name, raw in files.items():
            if name == "manifest.json" or name.startswith("systemd/"):
                continue
            target = stage / name
            _ensure_dir(target.parent, 0o755 if system_service else 0o700, guard, "stage_dir")
            mode = int(PAYLOAD_MODES[name], 8) if system_service else (0o700 if name.startswith("bin/") else 0o600)
            _write_file(target, raw, mode, guard, f"stage_payload:{name}")
        _write_file(stage / "manifest.json", files["manifest.json"], 0o644 if system_service else 0o600, guard, "stage_manifest")
        if not _tree_matches(stage, new_payload):
            raise PackageError("transaction_state_invalid")

        unit_backup = root / _UNIT_BACKUP_NAME
        _ensure_dir(unit_backup, 0o700, guard, "unit_backup")
        inventory_units: dict[str, dict | None] = {}
        for unit in UNITS:
            target = units / unit
            if target.exists():
                destination = unit_backup / unit
                _copy_file(target, destination, guard, f"unit_backup:{unit}")
                inventory_units[unit] = _path_file_identity(destination)
            else:
                inventory_units[unit] = None
        inventory_dropin: dict | None = None
        if dropin_present:
            _ensure_dir(unit_backup / _DROPIN_SERVICE_DIR, 0o700, guard, "dropin_backup_dir")
            destination = unit_backup / _DROPIN_BACKUP_RELATIVE
            _copy_file(dropin, destination, guard, "dropin_backup")
            inventory_dropin = _path_file_identity(destination)
        # The complete OLD payload identity is recorded BEFORE the retain so a
        # later restore can validate it and recognize an already-restored tree.
        inventory_payload = _inventory_tree(opt) if payload_present else None
        record = _publish(
            root, record, guard, phase="prepared",
            backups={"payload": inventory_payload, "units": inventory_units, "dropin": inventory_dropin},
        )

        payload_backup = root / _PAYLOAD_BACKUP_NAME
        if payload_present:
            _replace(opt, payload_backup, guard, "payload_retain")
        record = _publish(root, record, guard, phase="payload_retained")
        checkpoint("payload_old_retained")

        _replace(stage, opt, guard, "payload_publish")
        record = _publish(root, record, guard, phase="payload_published")
        checkpoint("payload_published")

        record = _publish(root, record, guard, phase="units_publishing")
        for unit in UNITS:
            target = units / unit
            if not unit_present[unit] and (target.exists() or target.is_symlink()):
                raise PackageError("transaction_state_invalid")
            # Publish the intent before the mutation so a torn write is still
            # classified as an owned NEW artifact by recovery.
            record = _publish(
                root, record, guard, published_units=[*record["published_units"], unit]
            )
            _write_file(target, files[f"systemd/{unit}"], 0o644 if system_service else 0o600, guard, f"unit_publish:{unit}")
            checkpoint(f"unit_published:{unit}")

        record = _publish(root, record, guard, phase="dropin_publishing")
        if publishes_dropin:
            record = _publish(root, record, guard, dropin_published=True)
            _write_file(dropin, _DROPIN_BYTES, 0o644, guard, "dropin_publish")

        # The authoritative commit is the durable publication of the committed
        # record: a failure before it restores OLD, a failure after it retains NEW.
        record = _publish(root, record, guard, phase="committed")
        _cleanup_owned(root, record, guard)
    except Exception:
        marker = root / TRANSACTION_MARKER
        if not marker.exists() and not marker.is_symlink():
            _cleanup_pre_record(root, stage, guard)
        else:
            durable = _load_record(root, marker)
            _assert_record_paths_safe(root, durable)
            if durable["phase"] == "committed":
                _assert_committed_active_new(root, durable)
                _cleanup_owned(root, durable, guard)
            elif durable["phase"] == "preparing":
                _cleanup_owned(root, durable, guard)
            else:
                _restore_old(root, durable, guard)
        raise
    if system_service:
        _installed_managed_contract(deadline_ns=deadline, _package=True)
        _require_managed_package_context()
    return {"version": manifest["version"], "manifest_sha256": _sha(files["manifest.json"])}


def uninstall_linux_package(root: Path) -> None:
    deadline = _managed_entry_deadline(180)
    managed = _managed_package_root(root)
    if managed:
        if not _MANAGED_PACKAGE.get():
            _managed_package_outer(None, "package-uninstall", deadline)
            return
        _require_managed_package_context()
    if managed:
        _installed_managed_contract(deadline_ns=deadline, _package=True)
        _recover_interrupted(root)
        _installed_managed_contract(deadline_ns=deadline, _package=True)
    opt = root / "opt/happyranch"
    _require_package_operand(opt, directory=True)
    for unit in UNITS:
        _require_package_operand(root / "etc/systemd/system" / unit, directory=False)
    if _backup_present(opt):
        _inventory_tree(opt, metadata_only=True)
    if opt.exists():
        if managed:
            _remove_tree(opt, None, "uninstall_payload")
        else:
            shutil.rmtree(opt)
    for unit in UNITS:
        path = root / "etc/systemd/system" / unit
        if path.exists():
            if managed:
                _unlink(path, None, "uninstall_unit")
            else:
                path.unlink()
    if managed:
        dropin = root / "etc/systemd/system" / _DROPIN_SERVICE_DIR / _DROPIN_FILE_NAME
        if dropin.exists():
            _unlink(dropin, None, "uninstall_dropin")
        _require_managed_package_context()
        if any(_backup_present(path) for path in (opt, *(root / "etc/systemd/system" / u for u in UNITS), dropin)):
            raise PackageError("managed_uninstall_incomplete")


def _package_managed_history(deadline_ns: int) -> tuple[Path | None, dict[str, Path], Path | None, bool]:
    """Select complete source-owned OLD/NEW without changing schema2 recovery.

    This is root package admission only. Runtime/deferred readers never call it.
    A marker supplies exact ownership, not a bypass of the composition reader.
    """
    root = Path("/")
    active = root / "opt/happyranch"
    units = root / "etc/systemd/system"
    dropin = units / _DROPIN_SERVICE_DIR / _DROPIN_FILE_NAME
    backup, saved_units, marker = _transaction_paths(root)

    def current() -> None:
        if time.monotonic_ns() >= deadline_ns:
            raise PackageError("managed_lifecycle_deadline")

    def owned(path: Path, *, private_container: bool = False) -> None:
        current()
        _assert_safe_target(root, path)
        if not _backup_present(path):
            return
        pending = [path]
        while pending:
            current()
            selected = pending.pop()
            metadata = selected.lstat()
            if (metadata.st_uid != 0 or metadata.st_mode & 0o022
                    or stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1
                    or not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode))):
                raise PackageError("managed_lifecycle_incompatible")
            if stat.S_ISDIR(metadata.st_mode):
                if private_container and stat.S_IMODE(metadata.st_mode) != 0o700:
                    raise PackageError("managed_lifecycle_incompatible")
                pending.extend(selected.iterdir())

    for parent in (Path("/opt"), Path("/etc"), Path("/etc/systemd"), units):
        _assert_safe_target(root, parent, directory=True)
        if _backup_present(parent):
            metadata = parent.lstat()
            if metadata.st_uid != 0 or metadata.st_mode & 0o022:
                raise PackageError("managed_lifecycle_incompatible")
    if _backup_present(dropin.parent):
        _assert_safe_target(root, dropin.parent, directory=True)
        if stat.S_IMODE(dropin.parent.lstat().st_mode) != 0o755 or {p.name for p in dropin.parent.iterdir()} - {_DROPIN_FILE_NAME}:
            raise PackageError("managed_lifecycle_incompatible")
    for path in (active, backup, saved_units, marker, _record_temp(root), dropin.parent):
        owned(path, private_container=path == saved_units)
    temporary = _record_temp(root)
    if _backup_present(temporary) and stat.S_IMODE(temporary.lstat().st_mode) != 0o600:
        raise PackageError("transaction_state_invalid")
    if not _backup_present(marker):
        if any(p.name.startswith(_RESIDUE_PREFIXES) for p in root.iterdir()):
            raise PackageError("managed_transaction_pending")
        if not _backup_present(active):
            if any(_backup_present(units / u) for u in UNITS) or _backup_present(dropin):
                raise PackageError("managed_lifecycle_incompatible")
            return None, {}, None, True
        for unit in UNITS:
            owned(units / unit)
        owned(dropin)
        current()
        return active, {u: units / u for u in UNITS}, dropin if _backup_present(dropin) else None, False
    if stat.S_IMODE(marker.lstat().st_mode) != 0o600:
        raise PackageError("transaction_state_invalid")
    record = _load_record(root, marker)
    _assert_record_paths_safe(root, record)
    stage = Path(record["stage"])
    allowed = {marker.name, _record_temp(root).name, backup.name, saved_units.name, stage.name}
    if any(p.name.startswith(_RESIDUE_PREFIXES) and p.name not in allowed for p in root.iterdir()):
        raise PackageError("transaction_state_invalid")
    owned(stage)
    # The exact declared NEW class is closed even when staging was interrupted
    # before its manifest was written. No arbitrary path/legacy inventory enters.
    new = record["new_payload"]
    names = {"bin", "share", "manifest.json"} | {n for n in PAYLOAD_MODES if not n.startswith("systemd/")}
    if new["root_mode"] != 0o755 or set(new["entries"]) != names:
        raise PackageError("managed_lifecycle_incompatible")
    for name, identity in new["entries"].items():
        directory = name in {"bin", "share"}
        mode = 0o755 if directory else 0o644 if name == "manifest.json" else int(PAYLOAD_MODES[name], 8)
        if (identity.get("type") != ("dir" if directory else "file") or identity.get("mode") != mode
                or not directory and re.fullmatch(r"[0-9a-f]{64}", identity["sha256"]) is None):
            raise PackageError("managed_lifecycle_incompatible")
    rendered = render_composite_units()
    expected_units = {u: {"mode": 0o644, "sha256": _sha(rendered[u].encode())} for u in UNITS}
    if record["new_units"] != expected_units or record["new_dropin"] not in (None, {"mode": 0o644, "sha256": _sha(_DROPIN_BYTES)}):
        raise PackageError("managed_lifecycle_incompatible")
    if _backup_present(stage):
        if record["phase"] == "preparing":
            # The marker owns this exact allocated stage before its first file
            # write. Interrupted writes/chmod can be partial; OLD is untouched.
            # Names/types/custody must still be a subset of the closed plan.
            partial = _inventory_tree(stage)
            if partial["root_mode"] != 0o755 or not set(partial["entries"]) <= set(new["entries"]):
                raise PackageError("transaction_state_invalid")
            for name, identity in partial["entries"].items():
                intended = new["entries"][name]
                if identity["type"] != intended["type"] or identity["mode"] not in {intended["mode"], 0o700 if intended["type"] == "dir" or name.startswith("bin/") else 0o600}:
                    raise PackageError("transaction_state_invalid")
        elif not _tree_is_owned_partial(stage, new):
            raise PackageError("transaction_state_invalid")
    if record["phase"] == "committed":
        _assert_committed_active_new(root, record)
        # Cleanup may already have consumed interior backup entries. Only an
        # exact surviving subset is owned, never arbitrary retained residue.
        old = record["backups"]["payload"]
        if _backup_present(backup) and (old is None or not _tree_is_owned_partial(backup, old)):
            raise PackageError("transaction_state_invalid")
        prefix = active
        selected_units = {u: units / u for u in UNITS}
        selected_dropin = dropin if _backup_present(dropin) else None
    elif record["phase"] == "preparing":
        # No OLD mutation is possible before prepared. Validate actual OLD in
        # full below; incomplete copies remain private and must be its prefix.
        if _backup_present(backup):
            raise PackageError("transaction_state_invalid")
        if _backup_present(active) != record["payload_present"] or any(
                _backup_present(units / u) != record["units"][u] for u in UNITS
        ) or _backup_present(dropin) != record["dropin_present"]:
            raise PackageError("transaction_state_invalid")
        prefix = active if record["payload_present"] else None
        selected_units = {u: units / u for u in UNITS} if prefix is not None else {}
        selected_dropin = dropin if record["dropin_present"] else None
    else:
        _classify_payload(root, record)
        _classify_units(root, record)
        _classify_dropin(root, record)
        old = record["backups"]["payload"]
        prefix = (backup if _backup_present(backup) else active) if old is not None else None
        if prefix is not None and not _tree_matches(prefix, old):
            raise PackageError("transaction_state_invalid")
        if _backup_present(active) and not (
                old is not None and _tree_matches(active, old)
                or _tree_matches(active, new)
                or record["phase"] == "rolling_back" and _tree_is_owned_partial(active, new)):
            raise PackageError("transaction_state_invalid")
        selected_units = {}
        for unit in UNITS:
            identity = record["backups"]["units"][unit]
            if identity is not None:
                selected_units[unit] = saved_units / unit if _backup_present(saved_units / unit) else units / unit
                if not _file_matches(selected_units[unit], identity):
                    raise PackageError("transaction_state_invalid")
        identity = record["backups"]["dropin"]
        selected_dropin = None if identity is None else (
            saved_units / _DROPIN_BACKUP_RELATIVE if _backup_present(saved_units / _DROPIN_BACKUP_RELATIVE) else dropin)
        if selected_dropin is not None and not _file_matches(selected_dropin, identity):
            raise PackageError("transaction_state_invalid")
    if (prefix is None and selected_units or prefix is not None and set(selected_units) != set(UNITS)):
        raise PackageError("managed_lifecycle_incompatible")
    # Validate fixed backups and in-progress active unit/drop-in writes, including
    # intent-before-truncation. Unknown files are preserved/refused before cleanup.
    if _backup_present(saved_units):
        expected_names = set(UNITS) | {_DROPIN_SERVICE_DIR}
        if any(p.name not in expected_names for p in saved_units.iterdir()):
            raise PackageError("transaction_state_invalid")
        subdir = saved_units / _DROPIN_SERVICE_DIR
        if _backup_present(subdir) and {p.name for p in subdir.iterdir()} - {_DROPIN_FILE_NAME}:
            raise PackageError("transaction_state_invalid")
    for unit in (*UNITS, _DROPIN_BACKUP_RELATIVE):
        is_dropin = unit == _DROPIN_BACKUP_RELATIVE
        target = dropin if is_dropin else units / unit
        saved = saved_units / unit
        old_identity = record["backups"]["dropin"] if is_dropin else record["backups"]["units"][unit]
        source = selected_dropin if is_dropin else selected_units.get(unit)
        for path in (target, saved):
            owned(path)
        if _backup_present(saved):
            if record["phase"] == "preparing":
                if source is None or not source.read_bytes().startswith(saved.read_bytes()) or stat.S_IMODE(saved.lstat().st_mode) not in {0o600, stat.S_IMODE(source.lstat().st_mode)}:
                    raise PackageError("transaction_state_invalid")
            elif old_identity is None or not _file_matches(saved, old_identity):
                raise PackageError("transaction_state_invalid")
        if _backup_present(target) and target != source:
            planned = _DROPIN_BYTES if is_dropin else rendered[unit].encode()
            published = record["dropin_published"] if is_dropin else unit in record["published_units"]
            if not (_file_matches(target, old_identity) or published and planned.startswith(target.read_bytes())
                    and stat.S_IMODE(target.lstat().st_mode) in {0o600, 0o644}):
                raise PackageError("transaction_state_invalid")
    current()
    return prefix, selected_units, selected_dropin, not record["payload_present"] and not any(record["units"].values()) and not record["dropin_present"]


def _installed_managed_contract(*, deadline_ns: int | None = None, _package: bool = False) -> dict[str, int]:
    """Independent current disk AND loaded PID1 proof, with no repair.

    Intended package inputs are the reviewed wheel build (pyproject runtime/cli
    and explicit data) and generate_inventory.py coordinates/license output.
    Digests authenticate those inputs; they do not certify arbitrary inputs as
    nonsecret. This reader cannot adopt a caller-selected installation.
    """
    deadline = (deadline_ns if _package and deadline_ns is not None else
                min(deadline_ns or time.monotonic_ns() + 2_000_000_000,
                    time.monotonic_ns() + 2_000_000_000))

    def current() -> None:
        if time.monotonic_ns() >= deadline:
            raise PackageError("managed_lifecycle_deadline")

    def directory(path: Path, mode: int | None = None) -> None:
        current()
        _assert_safe_target(Path("/"), path, directory=True)
        observed = path.lstat()
        if (not stat.S_ISDIR(observed.st_mode) or observed.st_uid != 0
                or observed.st_mode & 0o022
                or mode is not None and stat.S_IMODE(observed.st_mode) != mode):
            raise PackageError("managed_lifecycle_incompatible")

    def read(path: Path, mode: int, cap: int) -> bytes:
        current()
        _assert_safe_target(Path("/"), path, directory=False)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            before = os.fstat(descriptor)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != 0
                    or before.st_nlink != 1 or stat.S_IMODE(before.st_mode) != mode
                    or not 0 < before.st_size <= cap):
                raise PackageError("managed_lifecycle_incompatible")
            with os.fdopen(os.dup(descriptor), "rb") as stream:
                raw = stream.read(cap + 1)
            after = os.fstat(descriptor)
            named = path.lstat()
            stable = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
            if (len(raw) != before.st_size or any(getattr(before, k) != getattr(after, k) for k in stable)
                    or (named.st_dev, named.st_ino) != (after.st_dev, after.st_ino)):
                raise PackageError("managed_lifecycle_incompatible")
            current()
            return raw
        finally:
            os.close(descriptor)

    def properties(unit: str, names: tuple[str, ...]) -> dict[str, str]:
        from runtime.remote_access.cli import _capture_bounded_query_output, _close_and_reap_query_process
        current()
        bound = min(deadline / 1_000_000_000, time.monotonic() + 1)
        child = subprocess.Popen(["/usr/bin/systemctl", "show", unit, *["--property=" + n for n in names]],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL,
                                 env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
                                 pass_fds=(() if _MANAGED_LOCK.get() is None else (_MANAGED_LOCK.get(),)))
        try:
            raw, overflow = _capture_bounded_query_output(child, bound)
            remaining = bound - time.monotonic()
            if overflow or remaining <= 0 or child.wait(timeout=remaining) != 0:
                raise PackageError("managed_manager_unavailable")
        finally:
            _close_and_reap_query_process(child, min(bound, deadline / 1_000_000_000), managed=True)
        current()
        text = raw.decode("utf-8")
        values: dict[str, str] = {}
        if not text.endswith("\n"):
            raise PackageError("managed_manager_unavailable")
        for line in text[:-1].split("\n"):
            key, separator, value = line.partition("=")
            if (not separator or key not in names or key in values
                    or any(ord(c) < 32 or ord(c) == 127 for c in line)):
                raise PackageError("managed_manager_unavailable")
            values[key] = value
        if set(values) != set(names):
            raise PackageError("managed_manager_unavailable")
        return values

    try:
        if _package:
            if os.geteuid() != 0:
                raise PackageError("managed_package_context_required")
            prefix, unit_sources, selected_dropin, empty_prior = _package_managed_history(deadline_ns or deadline)
        else:
            prefix = Path("/opt/happyranch")
            unit_sources = {u: Path("/etc/systemd/system") / u for u in UNITS}
            selected_dropin = None
            empty_prior = False
        current()
        fresh = prefix is None
        private = not fresh and _package and stat.S_IMODE((prefix / "manifest.json").lstat().st_mode) == 0o600
        evidence_mode = 0o600 if private else 0o644
        share_mode = 0o700 if private else 0o755
        if fresh:
            # Explicit package bootstrap/recovery only: loaded absence is checked
            # below and the Go owner independently checks exact empty records.
            rendered = render_composite_units()
            dropin_present = False
        else:
            for path in (Path("/opt"), Path("/etc"), Path("/etc/systemd"), Path("/etc/systemd/system")):
                directory(path)
            directory(prefix, 0o755)
            directory(prefix / "bin", 0o755)
            directory(prefix / "share", share_mode)
            if {p.name for p in prefix.iterdir()} != {"manifest.json", "bin", "share"}:
                raise PackageError("managed_lifecycle_incompatible")
            if {p.name for p in (prefix / "bin").iterdir()} != {"happyranch-connector", "happyranch-tsnet-sidecar"}:
                raise PackageError("managed_lifecycle_incompatible")
            if {p.name for p in (prefix / "share").iterdir()} != {PurePosixPath(p).name for p in PAYLOAD_MODES if p.startswith("share/")}:
                raise PackageError("managed_lifecycle_incompatible")
            manifest = json.loads(read(prefix / "manifest.json", evidence_mode, 16384),
                                  object_pairs_hook=_reject_duplicate_keys)
            if (type(manifest) is not dict or set(manifest) != {
                    "schema_version", "version", "architecture", "sidecar_dependency_count",
                    "managed_lifecycle_contract", "files"}
                    or type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1
                    or manifest["architecture"] != "linux-amd64"
                    or not isinstance(manifest["version"], str) or not manifest["version"]):
                raise PackageError("managed_lifecycle_incompatible")
            contract = manifest["managed_lifecycle_contract"]
            if (type(contract) is not dict or contract != MANAGED_LIFECYCLE_CONTRACT
                    or any(type(value) is not int for value in contract.values())):
                raise PackageError("managed_lifecycle_incompatible")
            entries = manifest["files"]
            if (type(entries) is not list or len(entries) != len(PAYLOAD_MODES)
                    or any(type(e) is not dict or set(e) != {"path", "sha256", "mode"} for e in entries)
                    or {e["path"] for e in entries} != set(PAYLOAD_MODES)):
                raise PackageError("managed_lifecycle_incompatible")
            rendered = render_composite_units()
            payload: dict[str, bytes] = {}
            for entry in entries:
                relative = entry["path"]
                if (entry["mode"] != ("0o600" if private and not relative.startswith("bin/") else PAYLOAD_MODES[relative])
                        or not isinstance(entry["sha256"], str)
                        or re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) is None):
                    raise PackageError("managed_lifecycle_incompatible")
                path = (unit_sources[PurePosixPath(relative).name]
                        if relative.startswith("systemd/") else prefix / relative)
                raw = read(path, int(entry["mode"], 8), 256 * 1024 * 1024)
                if _sha(raw) != entry["sha256"]:
                    raise PackageError("managed_lifecycle_incompatible")
                if relative.startswith("systemd/") and raw != rendered[path.name].encode():
                    raise PackageError("managed_lifecycle_incompatible")
                payload[relative] = raw
            inventory = json.loads(payload["share/dependency-inventory.json"], object_pairs_hook=_reject_duplicate_keys)
            _validate_evidence(inventory, payload["share/THIRD_PARTY_NOTICES.md"])
            if (type(manifest["sidecar_dependency_count"]) is not int
                    or manifest["sidecar_dependency_count"] != len(inventory["modules"])
                    or payload["share/sbom.cdx.json"] != _sbom(inventory, manifest["version"])):
                raise PackageError("managed_lifecycle_incompatible")
            for entry in (() if _package else Path("/").iterdir()):
                current()
                if entry.name.startswith(_RESIDUE_PREFIXES):
                    raise PackageError("managed_transaction_pending")
            dropin_dir = Path("/etc/systemd/system") / _DROPIN_SERVICE_DIR
            dropin_present = False
            if not _package and dropin_dir.exists():
                directory(dropin_dir, None if _package else 0o755)
                dropin_names = {p.name for p in dropin_dir.iterdir()}
                if dropin_names not in (set(), {_DROPIN_FILE_NAME}):
                    raise PackageError("managed_lifecycle_incompatible")
                dropin_present = bool(dropin_names)
                if dropin_present and read(dropin_dir / _DROPIN_FILE_NAME, 0o644, 256) != _DROPIN_BYTES:
                    raise PackageError("managed_lifecycle_incompatible")
            if _package:
                dropin_present = selected_dropin is not None
                if selected_dropin is not None and (stat.S_IMODE(selected_dropin.lstat().st_mode) not in {0o600, 0o644} or read(selected_dropin, stat.S_IMODE(selected_dropin.lstat().st_mode), 256) != _DROPIN_BYTES):
                    raise PackageError("managed_lifecycle_incompatible")
        dropin_dir = Path("/etc/systemd/system") / _DROPIN_SERVICE_DIR
        for unit in UNITS:
            # An empty unexpected directory is also a changed unit input.
            if unit != UNITS[1] and (Path("/etc/systemd/system") / (unit + ".d")).exists():
                raise PackageError("managed_lifecycle_incompatible")
            base = properties(unit, ("LoadState", "FragmentPath", "DropInPaths", "NeedDaemonReload",
                                     "Wants", "Requires", "BindsTo", "PartOf", "After", "Before", "Conflicts",
                                     "DefaultDependencies", "StopWhenUnneeded", "RefuseManualStart", "RefuseManualStop"))
            if fresh or _package and (empty_prior or _MANAGED_FRESH.get()):
                if base["LoadState"] == "not-found":
                    if base["FragmentPath"] or base["DropInPaths"] or base["NeedDaemonReload"] != "no":
                        raise PackageError("managed_loaded_contract_mismatch")
                    continue
                # An owned fresh transaction can already have published units
                # observed by PID1. Any actually loaded selection must pass the
                # same complete property proof, never an absence assumption.
            if _package:
                # Actual PID1 mapping is independently closed even while fixed
                # disk units are in an owned intent-before-write transition.
                if base["DropInPaths"] not in ("", str(dropin_dir / _DROPIN_FILE_NAME)):
                    raise PackageError("managed_loaded_contract_mismatch")
                loaded_dropin = bool(base["DropInPaths"])
            else:
                loaded_dropin = dropin_present
            if (base["LoadState"] != "loaded" or base["NeedDaemonReload"] not in (("no", "yes") if _package else ("no",))
                    or base["FragmentPath"] != str(Path("/etc/systemd/system") / unit)
                    or base["DropInPaths"] != (str(dropin_dir / _DROPIN_FILE_NAME) if unit == UNITS[1] and loaded_dropin else "")):
                raise PackageError("managed_loaded_contract_mismatch")
            peers = set(UNITS) - {unit}
            selected = {name: set(base[name].split()) & peers for name in ("Wants", "Requires", "BindsTo", "PartOf", "After", "Before")}
            expected = ({"Wants": {UNITS[1]}, "Requires": set(), "BindsTo": set(),
                         "PartOf": {UNITS[1], UNITS[2]}, "After": set(), "Before": {UNITS[2]}}
                        if unit == UNITS[0] else
                        {"Wants": set(), "Requires": set(), "BindsTo": set(),
                         "PartOf": {UNITS[0], UNITS[2]}, "After": set(), "Before": {UNITS[2]}}
                        if unit == UNITS[1] else
                        {"Wants": {UNITS[0], UNITS[1]}, "Requires": set(), "BindsTo": set(),
                         "PartOf": set(), "After": {UNITS[0], UNITS[1]}, "Before": set()})
            if selected != expected:
                raise PackageError("managed_loaded_contract_mismatch")
            # Account for the bounded v255 default graph as well as explicit
            # edges; unknown unrelated dependencies cannot disappear through a
            # peer-only projection.
            wants = ({UNITS[0], UNITS[1]} if unit == UNITS[2]
                     else {"network-online.target", UNITS[1]} if unit == UNITS[0]
                     else {"network-online.target"})
            implicit_requires = {"sysinit.target", "system.slice", "tmp.mount", "var-tmp.mount"} if unit != UNITS[2] else set()
            implicit_after = implicit_requires | {"basic.target", "network-online.target", "systemd-journald.socket", "systemd-tmpfiles-setup.service"}
            if (set(base["Wants"].split()) != wants
                    or not set(base["Requires"].split()) <= implicit_requires
                    or base["BindsTo"]
                    or set(base["PartOf"].split()) != expected["PartOf"]
                    or unit != UNITS[2] and "network-online.target" not in set(base["After"].split())
                    or not set(base["After"].split()) <= expected["After"] | implicit_after
                    or not set(base["Before"].split()) <= expected["Before"] | {"shutdown.target"}
                    or set(base["Conflicts"].split()) != {"shutdown.target"}
                    or base["DefaultDependencies"] != "yes"
                    or any(base[k] != "no" for k in ("StopWhenUnneeded", "RefuseManualStart", "RefuseManualStop"))):
                raise PackageError("managed_loaded_contract_mismatch")
            if unit == UNITS[2]:
                continue
            scalar = {
                "User": "happyranch", "Group": "happyranch", "Type": "notify", "NotifyAccess": "main",
                "Restart": "on-failure" if unit == UNITS[0] else "no", "RestartUSec": "30s" if unit == UNITS[0] else "1s",
                "RestartMode": "normal", "StartLimitIntervalUSec": "0", "KillMode": "mixed",
                "TimeoutStartUSec": "2min", "TimeoutStopUSec": "35s", "TimeoutAbortUSec": "35s",
                "WatchdogUSec": "30s", "WatchdogSignal": "12", "NoNewPrivileges": "yes",
                "ProtectSystem": "strict", "ProtectHome": "yes", "PrivateTmp": "yes", "PrivateDevices": "yes",
                "ProtectKernelTunables": "yes", "ProtectKernelModules": "yes", "ProtectControlGroups": "yes",
                "RestrictSUIDSGID": "yes", "RestrictRealtime": "yes", "LockPersonality": "yes",
                "MemoryDenyWriteExecute": "yes", "CapabilityBoundingSet": "", "AmbientCapabilities": "", "UMask": "0077",
                "StateDirectory": "happyranch-connector" if unit == UNITS[0] else "happyranch-tsnet-sidecar",
                "StateDirectoryMode": "0700", "ExecStop": "", "ExecStopPost": "",
                "DynamicUser": "no", "SupplementaryGroups": "", "ReadWritePaths": "",
                "Environment": "", "EnvironmentFiles": "", "PassEnvironment": "",
                "LoadCredentialEncrypted": "", "RootDirectory": "", "RootImage": "",
            }
            if unit == UNITS[1]:
                del scalar["RestartUSec"]
            if properties(unit, tuple(scalar)) != scalar:
                raise PackageError("managed_loaded_contract_mismatch")
            commands = properties(unit, ("ExecStartEx", "ExecStartPreEx", "ExecStartPostEx", "LoadCredential", "RestartPreventExitStatus", "RestrictAddressFamilies", "SystemCallArchitectures"))
            unit_text = rendered[unit]
            for directive in ("ExecStart", "ExecStartPre", "ExecStartPost"):
                declared_raw = [line.split("=", 1)[1] for line in unit_text.splitlines() if line.startswith(directive + "=")]
                declared = [line.lstrip("+") for line in declared_raw]
                actual = commands[directive + "Ex"]
                # Closed systemctl execution tuple; dynamic execution observations
                # never supply configuration authority. Unknown framing refuses.
                records = re.findall(r"\{ path=([^ ;]+) ; argv\[\]=([^;]+?) ; flags=([^;]*) ; start_time=\[([^\]]*)\] ; stop_time=\[([^\]]*)\] ; pid=([0-9]+) ; code=([^;]+?) ; status=([^;]+?) \}", actual)
                if (len(records) != len(declared)
                        or " ".join(match.group(0) for match in re.finditer(r"\{[^{}]*\}", actual)) != actual
                        or [r[1] for r in records] != declared
                        or any(r[0] != r[1].split(" ", 1)[0]
                               or r[2].strip() != ("fully-privileged" if declaration.startswith("+") else "")
                               for r, declaration in zip(records, declared_raw))):
                    raise PackageError("managed_loaded_contract_mismatch")
            credentials = ("daemon.token:/etc/happyranch/daemon.token" if unit == UNITS[0]
                           else "enrollment.key:/etc/happyranch/enrollment.key" if loaded_dropin else "")
            prevented = " ".join(str(code) for code in range(2, 256)) if unit == UNITS[0] else ""
            if (commands["LoadCredential"] != credentials or commands["RestartPreventExitStatus"] != prevented
                    or set(commands["RestrictAddressFamilies"].split()) != {"AF_INET", "AF_INET6", "AF_UNIX", "AF_NETLINK"}
                    or commands["SystemCallArchitectures"] != "native"):
                raise PackageError("managed_loaded_contract_mismatch")
        current()
        return {"dropin_mode": int(PAYLOAD_MODES["systemd/" + UNITS[1]], 8), "dropin_directory_mode": 0o755}
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        raise PackageError("managed_lifecycle_incompatible") from exc


def require_managed_worker() -> None:
    """Pipe/env opt-in alone is not a foreground execution capability."""
    try:
        descriptor = int(os.environ["HAPPYRANCH_CHILD_HEALTH_FD"])
        if (descriptor != 3 or not stat.S_ISFIFO(os.fstat(descriptor).st_mode)
                or os.environ.get("HAPPYRANCH_CHILD_HEALTH_VERSION") != "2"):
            raise PackageError("managed_worker_refused")
        result = subprocess.run([_MANAGED_EXECUTABLE, "managed-worker-check"],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=2, check=False)
        if result.returncode != 0:
            raise PackageError("managed_worker_refused")
    except (KeyError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
        raise PackageError("managed_worker_refused") from exc


def managed_private_entry(operation: str) -> int:
    if operation == "eligibility":
        # These are the existing trust/config/policy readers, without the live
        # five-gate probe or a new credential/authorization algorithm.
        from runtime.remote_access.state_store import StateStoreError
        from runtime.remote_access.supervisor import (
            ConnectorConfig, ConnectorConfigError, ConnectorSupervisor, PolicyLoadError,
        )
        try:
            import errno
            deadline = int(os.environ.get("HAPPYRANCH_LIFECYCLE_DEADLINE_NS", "0"))
            if deadline <= time.monotonic_ns():
                raise PackageError("managed_lifecycle_deadline")
            inherited = _MANAGED_DEADLINE.get()
            deadline = min(deadline, inherited if inherited is not None else deadline)
            token = _MANAGED_DEADLINE.set(deadline)
            def current() -> None:
                if time.monotonic_ns() >= deadline:
                    raise PackageError("managed_lifecycle_deadline")
            current()
            _installed_managed_contract(deadline_ns=deadline)
            current()
            config = ConnectorConfig.from_file(Path("/etc/happyranch/connector.json"))
            current()
            if config.managed is None or config.unit_name != UNITS[0] or not config.system:
                raise PackageError("managed_configuration_invalid")
            supervisor = ConnectorSupervisor(config=config)
            current()
            # Observe the SAME expanded targets as the unchanged store. Only
            # positive ENOENT observations permit its configured default, at
            # every call; absence says nothing about former trust history.
            state_path = Path(config.state_path).expanduser()
            anchor_path = Path(str(state_path) + ".anchor")

            def trust_metadata(path: Path) -> tuple[bool, tuple]:
                # Walk literal parents, without resolve() following an alias.
                # A missing parent proves absence without creating directories.
                if not path.is_absolute():
                    path = Path.cwd() / path
                cursor = Path(path.anchor)
                observations = []
                components = (cursor, *path.parts[1:])
                for index, component in enumerate(components):
                    current()
                    if index:
                        cursor = cursor / component
                    try:
                        metadata = cursor.lstat()
                    except OSError as exc:
                        if exc.errno != errno.ENOENT:
                            raise
                        current()
                        observations.append((str(cursor), None))
                        return False, tuple(observations)
                    # Other OSErrors (including EACCES/ENOTDIR) reach the
                    # existing refusal path; neither is an absence certificate.
                    current()
                    leaf = index == len(components) - 1
                    if leaf:
                        if (not stat.S_ISREG(metadata.st_mode)
                                or metadata.st_nlink != 1
                                or stat.S_IMODE(metadata.st_mode) & 0o7077):
                            raise PackageError("managed_identity_invalid")
                    elif (not stat.S_ISDIR(metadata.st_mode)
                          or stat.S_IMODE(metadata.st_mode) & 0o022):
                        raise PackageError("managed_identity_invalid")
                    identity = (metadata.st_dev, metadata.st_ino,
                                metadata.st_mode, metadata.st_nlink,
                                metadata.st_uid, metadata.st_gid)
                    if leaf:
                        identity += (metadata.st_size, metadata.st_mtime_ns,
                                     metadata.st_ctime_ns)
                    observations.append((str(cursor), identity))
                return True, tuple(observations)

            before = (trust_metadata(state_path), trust_metadata(anchor_path))
            if before[0][0] != before[1][0]:
                raise PackageError("managed_identity_missing")
            current()
            state = supervisor.state_store.load()
            current()
            if state.connector_identity != supervisor._configured_identity():
                raise PackageError("managed_identity_invalid")
            supervisor.load_policy()
            current()
            after = (trust_metadata(state_path), trust_metadata(anchor_path))
            if after != before:
                raise PackageError("managed_identity_invalid")
            # Sampled bookends refuse observed movement, not every possible
            # intervening race/rollback. No retry, save or history claim follows.
            current()
            return 0
        except (OSError, RuntimeError, ValueError, AttributeError,
                StateStoreError, ConnectorConfigError, PolicyLoadError):
            return 78
        finally:
            if "token" in locals():
                _MANAGED_DEADLINE.reset(token)
    if operation in {"package-install", "package-uninstall"}:
        # Last entered guard/call boundary only: helper internals remain opaque.
        # Never inspect or serialize an exception, operand, path or stderr body.
        reason = "effective_uid"
        try:
            if os.geteuid() != 0:
                raise PackageError("managed_package_context_required")
            reason = "deadline"
            deadline = int(os.environ.get("HAPPYRANCH_LIFECYCLE_DEADLINE_NS", "0"))
            now = time.monotonic_ns()
            if not now < deadline <= now + 180_000_000_000:
                raise PackageError("managed_lifecycle_deadline")
            deadline_token = _MANAGED_DEADLINE.set(deadline)
            lock_token = _MANAGED_LOCK.set(3)
            package_token = _MANAGED_PACKAGE.set(True)
            try:
                reason = "context_before"
                _require_managed_package_context()
                import sys
                reason = "entry_input"
                entry = sys.stdin.buffer.read(64)
                if entry == b"package-admission\n":
                    reason = "admission_contract"
                    _installed_managed_contract(deadline_ns=deadline, _package=True)
                    reason = "admission_history"
                    prefix, _units, _dropin, _empty_prior = _package_managed_history(deadline)
                    reason = "admission_dropin"
                    if (operation == "package-install" and _dropin is not None
                            and stat.S_IMODE(_dropin.lstat().st_mode) != 0o644
                            and not Path("/etc/happyranch/enrollment.key").is_file()):
                        # There is no authorized new drop-in publication in this
                        # branch. Preserve private OLD; never silently widen it
                        # or call the resulting NEW normal-reader compatible.
                        raise PackageError("managed_lifecycle_incompatible")
                    if operation == "package-uninstall":
                        reason = "uninstall_prefix"
                        if prefix is None:
                            raise PackageError("managed_lifecycle_incompatible")
                        reason = "uninstall_images"
                        if (_sha((prefix / "bin/happyranch-connector").read_bytes()) != _sha(Path("/proc/self/exe").read_bytes())
                                or _sha((prefix / "bin/happyranch-tsnet-sidecar").read_bytes()) != _sha(Path("/proc/self/fd/6").read_bytes())):
                            raise PackageError("managed_lifecycle_incompatible")
                    reason = "admission_context_after"
                    _require_managed_package_context()
                    return 0
                if entry:
                    raise PackageError("managed_package_context_required")
                reason = "body_guard_before"
                _managed_guard_probe("package", 3, deadline)
                reason = "body_history"
                prefix, _unit_sources, _selected_dropin, empty_prior = _package_managed_history(deadline)
                fresh_token = _MANAGED_FRESH.set(prefix is None or empty_prior)
                reason = "body_contract"
                _installed_managed_contract(deadline_ns=deadline, _package=True)
                if operation == "package-install":
                    reason = "body_install"
                    result = install_linux_package(Path("/proc/self/fd/4"), Path("/"), system_service=True)
                else:
                    reason = "body_uninstall"
                    uninstall_linux_package(Path("/"))
                    result = {"state": "retired"}
                reason = "body_context_after"
                _require_managed_package_context()
                reason = "body_guard_after"
                _managed_guard_probe("package", 3, deadline)
                reason = "body_response"
                print(json.dumps(result, sort_keys=True, separators=(",", ":")))
                return 0
            finally:
                # Keep the original failing boundary through successful resets.
                failed_reason = reason
                reason = "context_release"
                if "fresh_token" in locals():
                    _MANAGED_FRESH.reset(fresh_token)
                _MANAGED_PACKAGE.reset(package_token)
                _MANAGED_LOCK.reset(lock_token)
                _MANAGED_DEADLINE.reset(deadline_token)
                reason = failed_reason
        except (OSError, RuntimeError, ValueError):
            try:
                os.write(2, b"managed_private_refusal:" + reason.encode("ascii") + b"\n")
            except OSError:
                pass
            return 78
    return 78
