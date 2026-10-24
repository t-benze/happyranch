#!/usr/bin/env python3
"""Parse bounded, structured sidecar diagnostics without retaining journal prose."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


RECEIPT_PREFIX = "diagnostic_receipt="
RECEIPT_GRAMMAR = {
    "credential_input": "input_acquisition",
    "engine_start": "engine_initialization",
    "network_join": "peer_establishment",
    "durable_commit": "receipt_commit",
    "connector": "connector_probe",
    "listener": "listener_admission",
    "configuration": "configuration_validation",
}
SIDECAR_UNIT = "happyranch-tsnet-sidecar.service"
SIDECAR_ACTOR = "tsnet-sidecar"
N3_UNITS = {
    "happyranch-managed.target",
    "happyranch-connector.service",
    SIDECAR_UNIT,
}
START_BEGIN_MESSAGE_ID = "7d4958e842da4a758f6c1cdc7b36dcc5"
START_SUCCESS_MESSAGE_ID = "39f53479d3a045ac8e11786248231fbf"
START_FAILURE_MESSAGE_ID = "be02cf6855d2428ba40df7e9d022f03d"
START_FAILURE_RESULTS = {
    "timeout",
    "failed",
    "dependency",
    "assert",
    "unsupported",
    "collected",
    "once",
}


def _compact_hex(value: object, *, size: int) -> str | None:
    """Normalize systemd's hyphenated and journal's compact ID spellings."""
    if not isinstance(value, str):
        return None
    compact = value.replace("-", "").lower()
    if len(compact) != size or any(character not in "0123456789abcdef" for character in compact):
        return None
    return compact


NETWORK_REASONS = frozenset({
    "unclassified", "context_cancelled", "deadline_exceeded", "up_backend_error", "up_no_ip",
    "up_error_unclassified", "up_status_unavailable", "up_not_running", "peer_status_error",
    "peer_status_unavailable", "peer_not_running", "peer_wait_deadline", "expected_peer_missing",
})
RECEIPT_KEYS = frozenset({"category", "phase", "actor", "unit", "outcome", "terminal", "assertion"})


def _unique_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate member")
        value[key] = item
    return value


def _invalid_constant(_: str) -> None:
    raise ValueError("nonfinite value")


def _finite_float(value: str) -> float:
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError("nonfinite value")
    return number


def _closed_json(raw: str) -> Any:
    # Decode the whole frame before attribution, including ignored members.
    return json.loads(raw, object_pairs_hook=_unique_members,
                      parse_constant=_invalid_constant, parse_float=_finite_float)


def _receipt(message: str, *, allow_unknown: bool = False, full: bool = False) -> dict[str, Any] | None:
    if not message.startswith(RECEIPT_PREFIX):
        return None
    try:
        raw = _closed_json(message.removeprefix(RECEIPT_PREFIX))
    except (TypeError, ValueError, RecursionError):
        return {}
    if not isinstance(raw, dict) or set(raw) not in (RECEIPT_KEYS, RECEIPT_KEYS | {"sub_reason"}):
        return {}
    category, phase = raw.get("category"), raw.get("phase")
    if (
        not isinstance(category, str) or not isinstance(phase, str)
        or not (RECEIPT_GRAMMAR.get(category) == phase or allow_unknown and category == phase == "unknown")
        or raw["actor"] != SIDECAR_ACTOR or raw["unit"] != SIDECAR_UNIT
        or raw["outcome"] != "failed" or raw["terminal"] is not True
        or raw["assertion"] != {"status": "completed"}
    ):
        return {}
    result = {"category": category, "phase": phase}
    if "sub_reason" in raw:
        reason = raw["sub_reason"]
        if category != "network_join" or not isinstance(reason, str) or reason not in NETWORK_REASONS:
            return {}
        result["sub_reason"] = reason
    if full:
        result.update(actor=SIDECAR_ACTOR, unit=SIDECAR_UNIT, outcome="failed", terminal=True, assertion={"status": "completed"})
    return result


def collect(*, lines: list[str], invocation_id: str, boot_id: str, since_us: int, until_us: int) -> dict[str, Any]:
    """Return only allowed receipt values and closed collection-loss terms."""
    receipts: list[dict[str, str]] = []
    losses: set[str] = set()
    saw_record = False
    expected_invocation = _compact_hex(invocation_id, size=32)
    expected_boot = _compact_hex(boot_id, size=32)
    if expected_invocation is None or expected_boot is None or since_us > until_us:
        return {"receipts": [], "losses": ["parse_loss"]}
    for line in lines:
        try:
            event = _closed_json(line)
        except (ValueError, RecursionError):
            losses.add("parse_loss")
            continue
        if not isinstance(event, dict):
            losses.add("parse_loss")
            continue
        message = event.get("MESSAGE")
        if not isinstance(message, str) or not message.startswith(RECEIPT_PREFIX):
            continue
        saw_record = True
        timestamp = event.get("__REALTIME_TIMESTAMP")
        try:
            # Match the existing journal realtime/monotonic timestamp domain.
            if not isinstance(timestamp, str) or re.fullmatch(r"[1-9][0-9]{0,17}", timestamp) is None:
                raise ValueError("time")
            timestamp_us = int(timestamp)
        except ValueError:
            losses.add("parse_loss")
            continue
        if (
            event.get("_SYSTEMD_UNIT") != SIDECAR_UNIT
            or _compact_hex(event.get("_BOOT_ID"), size=32) != expected_boot
            or _compact_hex(event.get("_SYSTEMD_INVOCATION_ID"), size=32) != expected_invocation
            or not since_us <= timestamp_us <= until_us
        ):
            losses.add("attribution_loss")
            continue
        parsed = _receipt(message)
        if parsed is None:
            continue
        if not parsed:
            losses.add("parse_loss")
            continue
        receipts.append(parsed)
    if not saw_record and not losses:
        losses.add("empty")
    return {"receipts": receipts, "losses": sorted(losses) or ["observed"]}


def collect_jobs(*, lines: list[str], boot_id: str, since_us: int, until_us: int) -> dict[str, Any]:
    """Return attributed completed start jobs without retaining journal prose."""
    jobs: list[dict[str, int | str]] = []
    losses: set[str] = set()
    seen_job_ids: set[int] = set()
    expected_boot = _compact_hex(boot_id, size=32)
    if expected_boot is None or since_us > until_us:
        return {"jobs": [], "loss": "parse_loss"}
    for line in lines:
        try:
            event = _closed_json(line)
        except (ValueError, RecursionError):
            losses.add("parse_loss")
            continue
        if not isinstance(event, dict):
            losses.add("parse_loss")
            continue
        unit = event.get("UNIT")
        job_type = event.get("JOB_TYPE")
        if not isinstance(unit, str) or unit not in N3_UNITS or job_type != "start":
            continue
        message_id = _compact_hex(event.get("MESSAGE_ID"), size=32)
        if message_id == START_BEGIN_MESSAGE_ID:
            continue
        if message_id not in {START_SUCCESS_MESSAGE_ID, START_FAILURE_MESSAGE_ID}:
            continue
        if event.get("_PID") != "1" or event.get("_UID") != "0":
            losses.add("attribution_loss")
            continue
        timestamp = event.get("__REALTIME_TIMESTAMP")
        try:
            # Match the existing journal realtime/monotonic timestamp domain.
            if not isinstance(timestamp, str) or re.fullmatch(r"[1-9][0-9]{0,17}", timestamp) is None:
                raise ValueError("time")
            timestamp_us = int(timestamp)
        except ValueError:
            losses.add("parse_loss")
            continue
        if _compact_hex(event.get("_BOOT_ID"), size=32) != expected_boot or not since_us <= timestamp_us <= until_us:
            losses.add("attribution_loss")
            continue
        job_id_text = event.get("JOB_ID")
        result = event.get("JOB_RESULT")
        if (
            not isinstance(job_id_text, str)
            or not job_id_text.isdecimal()
            or not 1 <= len(job_id_text) <= 10
            or not 0 < int(job_id_text) <= 4_294_967_295
            or not isinstance(result, str)
            or (message_id == START_SUCCESS_MESSAGE_ID and result != "done")
            or (message_id == START_FAILURE_MESSAGE_ID and result not in START_FAILURE_RESULTS)
        ):
            losses.add("parse_loss")
            continue
        job_id = int(job_id_text)
        if job_id in seen_job_ids:
            losses.add("parse_loss")
            continue
        seen_job_ids.add(job_id)
        jobs.append({"id": job_id, "unit": unit, "type": "start", "result": result})
    loss = "parse_loss" if "parse_loss" in losses else "attribution_loss" if losses else "observed" if jobs else "empty"
    return {"jobs": jobs, "loss": loss}



def filter_plain() -> None:
    """Project before retained-output caps, with bounded raw line storage."""
    pending = bytearray()
    overlong = False
    while chunk := sys.stdin.buffer.read1(4096):
        for part_index, part in enumerate(chunk.split(b"\n")):
            if part_index:
                if not overlong:
                    try:
                        line = pending.decode("utf-8")
                    except UnicodeError:
                        line = ""
                    if line in {"readiness_unavailable", "watchdog_unavailable"}:
                        print(line, flush=True)
                    else:
                        receipt = _receipt(line, allow_unknown=True, full=True)
                        if receipt:
                            print(RECEIPT_PREFIX + json.dumps(receipt, sort_keys=True, separators=(",", ":")), flush=True)
                pending.clear()
                overlong = False
            if not overlong:
                if len(pending) + len(part) > 16384:
                    pending.clear()
                    overlong = True
                else:
                    pending.extend(part)
    # Journal cat records are complete lines; a partial final raw record is
    # never promoted to a receipt.


HEADSCALE_BYTES = 65536
HEADSCALE_LINES = 256
HEADSCALE_NODES = 64
HEADSCALE_OUTPUT = 2048
HEADSCALE_ROLES = {"peer": "synthetic-peer-ci", "sidecar": "home-sidecar-ci"}
# Headscale v0.25.1 (ade7f2a2): these producers call Caller(), then Msg().
# Zerolog v1.33.0 uses level/caller/message; Err and key/SQL fields are never
# projected. Its CLI hardwires ConsoleWriter despite log.format=json, so retain
# that pinned format too. No auth-key refusal event is emitted by auth.go or
# noiseServer.NoiseRegistrationHandler; do not invent one from generic errors.
HEADSCALE_EVENTS = {
    "unsupported client connected": ("error", "hscontrol/noise.go", "unsupported_client"),
    "No Upgrade header in TS2021 request. If headscale is behind a reverse proxy, make sure it is configured to pass WebSockets through.": ("warn", "hscontrol/noise.go", "missing_upgrade"),
    "Error initializing": ("fatal", "cmd/headscale/cli/serve.go", "initialization_error"),
    "Headscale ran into an error and had to shut down.": ("fatal", "cmd/headscale/cli/serve.go", "server_shutdown_error"),
}


def _unknown_nodes(loss: str) -> dict[str, Any]:
    return {**{role: {"count": None, "state": "unknown"} for role in HEADSCALE_ROLES}, "losses": [loss]}


def project_headscale_nodes(raw: bytes) -> dict[str, Any]:
    """Project the pinned v0.25.1 Node JSON; no identity fallback or prose."""
    if len(raw) > HEADSCALE_BYTES:
        return _unknown_nodes("truncated")
    try:
        rows = _closed_json(raw.decode("utf-8"))
    except (UnicodeError, ValueError, RecursionError):
        return _unknown_nodes("parse_loss")
    if rows is None:
        rows = []  # pinned encoding/json nil-slice representation, exit0 only
    if not isinstance(rows, list):
        return _unknown_nodes("parse_loss")
    if len(rows) > HEADSCALE_NODES:
        return _unknown_nodes("truncated")
    observed: dict[str, list[bool]] = {role: [] for role in HEADSCALE_ROLES}
    partial = False
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str) or not row["name"] or any(ord(c) < 32 or ord(c) == 127 for c in row["name"]):
            partial = True
            continue
        for role, name in HEADSCALE_ROLES.items():
            if row["name"] != name:
                continue
            # Node.online is bool with json:"online,omitempty" in v0.25.1.
            online = row.get("online", False)
            if not isinstance(online, bool):
                partial = True
            else:
                observed[role].append(online)
    result: dict[str, Any] = {}
    for role, states in observed.items():
        state = "unknown" if partial else "absent" if not states else "online" if all(states) else "offline" if not any(states) else "mixed"
        result[role] = {"count": len(states), "state": state}
    result["losses"] = ["parse_loss" if partial else "observed" if rows else "empty"]
    return result


def project_headscale_log(raw: bytes) -> dict[str, Any]:
    """Bounded fixture history, without attribution to any sidecar attempt."""
    losses: set[str] = set()
    if len(raw) > HEADSCALE_BYTES:
        losses.add("truncated")
        raw = raw[:HEADSCALE_BYTES]
    lines = raw.splitlines(keepends=True)
    if len(lines) > HEADSCALE_LINES:
        losses.add("truncated")
    counts: dict[str, int] = {}
    for line in lines[:HEADSCALE_LINES]:
        if not line.endswith(b"\n"):
            losses.add("parse_loss")
            continue
        try:
            text = line.decode("utf-8").rstrip("\r\n")
            if text.startswith("{"):
                record = _closed_json(text)
                if not isinstance(record, dict):
                    raise ValueError
                level, caller, message = record.get("level"), record.get("caller", ""), record.get("message", "")
                if not isinstance(level, str) or level not in {"trace", "debug", "info", "warn", "error", "fatal", "panic"} or not isinstance(caller, str) or not isinstance(message, str) or any(ord(c) < 32 or ord(c) == 127 for c in message + caller):
                    raise ValueError
            else:
                # Pinned cmd/headscale/headscale.go: ConsoleWriter RFC3339,
                # with optional SGR styling; no arbitrary text/error matching.
                text = re.sub(r"\x1b\[[0-9;]*m", "", text)
                if any(ord(c) < 32 or ord(c) == 127 for c in text):
                    raise ValueError
                match = re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d) (TRC|DBG|INF|WRN|ERR|FTL|PNC) (?:(\S+\.go:\d+) > )?(.+)", text)
                if match is None:
                    raise ValueError
                severity, caller, message = match.groups()
                level = {"TRC": "trace", "DBG": "debug", "INF": "info", "WRN": "warn", "ERR": "error", "FTL": "fatal", "PNC": "panic"}[severity]
                caller = caller or ""
                # The console message precedes zerolog's key=value suffix.
                message = re.split(r" (?=[A-Za-z_][A-Za-z_0-9]*=)", message, maxsplit=1)[0]
            event = "unclassified"
            expected = HEADSCALE_EVENTS.get(message)
            if expected and level == expected[0] and re.search(r"(?:^|/)" + re.escape(expected[1]) + r":\d+$", caller):
                event = expected[2]
            counts[event] = counts.get(event, 0) + 1
        except (UnicodeError, ValueError, RecursionError):
            losses.add("parse_loss")
    return {"events": [{"event": event, "count": count} for event, count in sorted(counts.items())], "losses": sorted(losses) if losses else ["observed" if counts else "empty"]}


def _initialize_observation_owner(*, require_root: bool = False) -> bool:
    """Establish adoption in THIS dedicated observer before spawning a query."""
    try:
        if not sys.platform.startswith("linux"):
            return False
        # The privileged Stop/peer queries execute directly under this same UID;
        # no inner sudo/session monitor can outlive or outrank their cleanup owner.
        if require_root and os.getresuid() != (0, 0, 0):
            return False
        # A dedicated owner must retain child statuses rather than auto-reap.
        signal.signal(signal.SIGCHLD, signal.SIG_DFL)
        if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
            return False
        libc = ctypes.CDLL(None, use_errno=True)
        enabled = ctypes.c_int()
        return (libc.prctl(36, 1, 0, 0, 0) == 0  # PR_SET_CHILD_SUBREAPER
                and libc.prctl(37, ctypes.byref(enabled), 0, 0, 0) == 0
                and enabled.value == 1)  # PR_GET_CHILD_SUBREAPER
    except (OSError, AttributeError, ValueError):
        return False


def _terminate_observation(process: subprocess.Popen[bytes]) -> bool:
    """Terminate and confirm reap of the owned query group, including pipe holders."""
    deadline = time.monotonic() + 1
    confirmed = True
    for signum in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, signum)
        except ProcessLookupError:
            pass
        except OSError:
            confirmed = False
        try:
            process.wait(timeout=min(0.05, max(0, deadline - time.monotonic())))
        except subprocess.TimeoutExpired:
            pass
        except OSError:
            confirmed = False
    # Subreaper adoption, not group membership, grants waitpid ownership.
    # The finite read-only query commands retain this session/process group.
    # Never wait on unrelated fixture children or infer reap from leader exit.
    while time.monotonic() < deadline:
        try:
            child, _ = os.waitpid(-process.pid, os.WNOHANG)
        except ChildProcessError:
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                return confirmed and process.poll() is not None
            except OSError:
                return False
            # No adoptable child yet is not proof that the group is gone.
            child = 0
        except OSError:
            return False
        if child == 0:
            time.sleep(min(0.01, max(0, deadline - time.monotonic())))
    return False


def _bounded_observation(argv: list[str], *, seconds: float, cap: int, data: bytes | None = None, require_root: bool = False) -> tuple[bytes, str]:
    """An independent absolute launch/write/read/wait deadline and byte cap."""
    deadline = time.monotonic() + seconds
    if not _initialize_observation_owner(require_root=require_root):
        return b"", "launch_failure"
    process = None
    cancelled = 0
    signals = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
    handlers = {item: signal.getsignal(item) for item in signals}

    def defer_cancel(signum: int, _frame: Any) -> None:
        nonlocal cancelled
        # Keep this nonthrowing for the ENTIRE owned lifetime, including the
        # first instruction of cleanup. Read checkpoints propagate cancellation.
        cancelled = cancelled or signum

    def read() -> tuple[bytes, str]:
        assert process is not None
        output = bytearray()
        total = 0
        written = 0
        if time.monotonic() >= deadline:
            return b"", "timeout"
        with selectors.DefaultSelector() as selector:
            for pipe in (process.stdout, process.stderr):
                assert pipe is not None
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ)
            if process.stdin is not None:
                os.set_blocking(process.stdin.fileno(), False)
                selector.register(process.stdin, selectors.EVENT_WRITE)
            while selector.get_map():
                if cancelled:
                    raise SystemExit(128 + cancelled)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return b"", "timeout"
                for key, _ in selector.select(min(remaining, 0.05)):
                    if cancelled:
                        raise SystemExit(128 + cancelled)
                    if time.monotonic() >= deadline:
                        return b"", "timeout"
                    if key.fileobj is process.stdin:
                        try:
                            written += os.write(key.fd, data[written:written + 4096])  # type: ignore[index]
                        except BrokenPipeError:
                            written = len(data or b"")
                        if time.monotonic() >= deadline:
                            return b"", "timeout"
                        if written == len(data or b""):
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                        continue
                    chunk = os.read(key.fd, min(4096, cap + 1 - total))
                    if time.monotonic() >= deadline:
                        return b"", "timeout"
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    total += len(chunk)
                    if total > cap:
                        return b"", "truncated"
                    if key.fileobj is process.stdout:
                        output.extend(chunk)
            while True:
                if cancelled:
                    raise SystemExit(128 + cancelled)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return b"", "timeout"
                try:
                    status = process.wait(timeout=min(remaining, 0.05))
                    break
                except subprocess.TimeoutExpired:
                    continue
            if time.monotonic() >= deadline:
                return b"", "timeout"
            if status != 0:
                return b"", "query_error"
            payload = bytes(output)
            if time.monotonic() >= deadline:
                return b"", "timeout"
            return payload, "observed"

    try:
        for item in signals:
            signal.signal(item, defer_cancel)
        if cancelled:
            raise SystemExit(128 + cancelled)
        try:
            process = subprocess.Popen(argv, stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        except OSError:
            result = (b"", "launch_failure")
        if cancelled:
            raise SystemExit(128 + cancelled)
        if process is not None:
            result = read()
    except OSError:
        result = (b"", "query_error")
    finally:
        cleaned = True
        try:
            # The launch/read handlers are still nonthrowing here: no handler
            # switch or unprotected transition can abandon an acquired handle.
            try:
                if process is not None:
                    cleaned = _terminate_observation(process)
            except OSError:
                cleaned = False
            finally:
                if process is not None:
                    for pipe in (process.stdin, process.stdout, process.stderr):
                        if pipe is not None:
                            try:
                                pipe.close()
                            except OSError:
                                cleaned = False
        finally:
            # Restore only after termination/reap and every pipe-close attempt.
            for item, handler in handlers.items():
                signal.signal(item, handler)
    if cancelled:
        raise SystemExit(128 + cancelled)
    return result if cleaned else (b"", "query_error")


def capture_headscale(work: Path | None, pid: str | None) -> dict[str, Any]:
    """Read only the existing fixture; every stage owns its reserved budget."""
    result: dict[str, Any] = {"process_state": "unknown", "log": {"events": [], "losses": ["unavailable"]}, "nodes": _unknown_nodes("unavailable")}
    worker = [sys.executable, str(Path(__file__).resolve())]
    if pid is not None and pid.isascii() and pid.isdecimal() and 0 < int(pid) <= 2_147_483_647:
        state, loss = _bounded_observation(worker + ["--mode", "headscale-process", "--headscale-pid", pid], seconds=1, cap=32)
        if loss == "observed" and state.strip() in {b"running", b"not_running", b"unknown"}:
            result["process_state"] = state.decode().strip()
    if work is None:
        return result
    log_path = work / "headscale.log"
    try:
        available = stat.S_ISREG(log_path.lstat().st_mode) and os.access(log_path, os.R_OK)
    except OSError:
        available = False
    if available:
        raw, loss = _bounded_observation(worker + ["--mode", "headscale-read", "--input", str(log_path)], seconds=1, cap=HEADSCALE_BYTES + 1)
        if loss == "observed":
            parsed, parser_loss = _bounded_observation(worker + ["--mode", "headscale-log"], seconds=1, cap=HEADSCALE_OUTPUT, data=raw)
            if parser_loss == "observed":
                try:
                    result["log"] = _closed_json(parsed.decode())
                except (ValueError, UnicodeError, RecursionError):
                    result["log"] = {"events": [], "losses": ["parse_loss"]}
            else:
                result["log"] = {"events": [], "losses": [parser_loss]}
        else:
            result["log"] = {"events": [], "losses": [loss]}
    binary, config = work / "headscale", work / "hs/config.yaml"
    if binary.is_file() and os.access(binary, os.X_OK) and config.is_file():
        raw, loss = _bounded_observation([str(binary), "nodes", "list", "--config", str(config), "--output", "json"], seconds=3, cap=HEADSCALE_BYTES)
        if loss != "observed":
            result["nodes"] = _unknown_nodes(loss)
        else:
            parsed, parser_loss = _bounded_observation(worker + ["--mode", "headscale-nodes"], seconds=1, cap=HEADSCALE_OUTPUT, data=raw)
            if parser_loss != "observed":
                result["nodes"] = _unknown_nodes(parser_loss)
            else:
                try:
                    result["nodes"] = _closed_json(parsed.decode())
                except (ValueError, UnicodeError, RecursionError):
                    result["nodes"] = _unknown_nodes("parse_loss")
    return result

CONNECTOR_UNIT = "happyranch-connector.service"
CONNECTOR_PROPERTIES = frozenset({"MainPID", "InvocationID", "ActiveState", "SubState", "StatusText"})
CONNECTOR_GATES = ("daemon_loopback", "credential_permissions", "current_policy", "bind_identity", "trust_state")
CONNECTOR_STATUS = {
    "": "not_reported",
    "composite healthy": "composite_healthy",
    "connector stopping": "stopping",
    "connector exited": "exited",
}


CREDENTIAL_MODE_LOSSES = frozenset({
    "observed", "unattempted", "unavailable_namespace", "unavailable_location",
    "query_error", "unsafe_type", "attribution_loss", "timeout", "truncated",
    "parse_loss", "launch_failure", "unavailable_process", "unavailable_child", "absent", "failed",
})


def _credential_mode_unknown(loss: str) -> dict[str, Any]:
    return {"location": "systemd-daemon-token", "namespace": "connector-service-root",
            "mode": None, "observed_at_us": None, "loss": loss}


def _connector_credential_mode(pid: int) -> dict[str, Any]:
    """Metadata only in the existing MainPID's root view, never token reads.

    A missing namespace/location is unavailable, not host-path absence proof.
    No ACL membership, service readability or internal gate is inferred.
    """
    root = Path(f"/proc/{pid}/root")
    namespace = Path(f"/proc/{pid}/ns/mnt")
    directory = root / "run/credentials" / CONNECTOR_UNIT
    path = directory / "daemon.token"
    try:
        mount_identity = namespace.stat()
        if not root.is_dir():
            return _credential_mode_unknown("unavailable_namespace")
        directory_metadata = directory.lstat()
        if not stat.S_ISDIR(directory_metadata.st_mode):
            return _credential_mode_unknown("unsafe_type")
    except FileNotFoundError:
        return _credential_mode_unknown("unavailable_namespace")
    except OSError:
        return _credential_mode_unknown("query_error")
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            return _credential_mode_unknown("unsafe_type")
        observed_at_us = time.time_ns() // 1000
        # Detect ordinary namespace/file drift without serializing identities.
        confirmed = path.lstat()
        mount_confirmed = namespace.stat()
        if (metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_ctime_ns) != (
            confirmed.st_dev, confirmed.st_ino, confirmed.st_mode, confirmed.st_ctime_ns
        ) or (mount_identity.st_dev, mount_identity.st_ino) != (mount_confirmed.st_dev, mount_confirmed.st_ino):
            return _credential_mode_unknown("attribution_loss")
        result = _credential_mode_unknown("observed")
        result.update(mode=f"{stat.S_IMODE(metadata.st_mode):04o}", observed_at_us=observed_at_us)
        return result
    except FileNotFoundError:
        return _credential_mode_unknown("unavailable_location")
    except OSError:
        return _credential_mode_unknown("query_error")


def _credential_mode_sample(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {"location", "namespace", "mode", "observed_at_us", "loss"}:
        raise ValueError
    if value["location"] != "systemd-daemon-token" or value["namespace"] != "connector-service-root" or value["loss"] not in CREDENTIAL_MODE_LOSSES:
        raise ValueError
    if value["loss"] == "observed":
        if not isinstance(value["mode"], str) or re.fullmatch(r"[0-7]{4}", value["mode"]) is None:
            raise ValueError
        if type(value["observed_at_us"]) is not int or not 0 < value["observed_at_us"] < 10**18:
            raise ValueError
    elif value["mode"] is not None or value["observed_at_us"] is not None:
        raise ValueError


def _connector_unknown(loss: str) -> dict[str, Any]:
    return {"unit": CONNECTOR_UNIT, "binding": None, "listening": None, "bound_port": None,
            "status": "unavailable", "loss": loss,
            "credential_mode": _credential_mode_unknown(loss if loss in CREDENTIAL_MODE_LOSSES else "unattempted"),
            "collection": {"observation_budget_seconds": 6, "command_budget_seconds": 8, "kill_grace_seconds": 1, "output_cap_bytes": 2048},
            "readiness_gates": {name: {"ok": None, "loss": "unavailable_internal"} for name in CONNECTOR_GATES}}


def _connector_properties(raw: bytes) -> dict[str, str]:
    # Reject all unsupported framing before any value normalization. Unknown
    # StatusText is dropped, never retained as a gate or exception message.
    text = raw.decode("ascii")
    if any(ord(char) < 32 and char != "\n" or ord(char) == 127 for char in text):
        raise ValueError
    rows = text.removesuffix("\n").split("\n")
    result: dict[str, str] = {}
    for row in rows:
        key, separator, value = row.partition("=")
        if not separator or key not in CONNECTOR_PROPERTIES or key in result:
            raise ValueError
        result[key] = value
    if set(result) != CONNECTOR_PROPERTIES:
        raise ValueError
    if not re.fullmatch(r"0|[1-9][0-9]{0,9}", result["MainPID"]) or int(result["MainPID"]) > 2_147_483_647:
        raise ValueError
    if result["ActiveState"] not in {"active", "inactive", "activating", "deactivating", "failed"}:
        raise ValueError
    if result["SubState"] not in {"running", "dead", "failed", "exited", "start", "start-pre", "start-post", "stop", "stop-sigterm", "stop-sigkill", "stop-post", "auto-restart", "auto-restart-queued", "final-sigterm", "final-sigkill"}:
        raise ValueError
    if int(result["MainPID"]) and _compact_hex(result["InvocationID"], size=32) is None:
        raise ValueError
    return result


def _proc_bytes(path: Path, cap: int) -> bytes:
    with path.open("rb") as stream:
        raw = stream.read(cap + 1)
    if len(raw) > cap:
        raise OverflowError
    return raw


def _proc_identity(pid: int) -> tuple[int, int, bytes]:
    raw = _proc_bytes(Path(f"/proc/{pid}/stat"), 4096)
    # comm is arbitrary parenthesized text: split at its final closing paren.
    leader, separator, tail = raw.rpartition(b") ")
    if not separator or not leader.startswith(f"{pid} (".encode()):
        raise ValueError
    fields = tail.split()
    if len(fields) < 20 or not fields[1].isdigit() or not fields[19].isdigit() or fields[0] in {b"Z", b"X", b"x"}:
        raise ValueError
    cgroup = _proc_bytes(Path(f"/proc/{pid}/cgroup"), 4096)
    rows = cgroup.splitlines()
    if len(rows) != 1 or not rows[0].startswith(b"0::/") or not rows[0].endswith(b"/" + CONNECTOR_UNIT.encode()):
        raise ValueError
    return int(fields[1]), int(fields[19]), cgroup


def _connector_process(pid: int) -> dict[str, Any]:
    """MainPID -> bounded descendants (including onefile worker) -> sockets.

    All reads/iterations are capped. Repeated complete process/child/FD sets
    detect ordinary drift, not hostile ABA or an atomic kernel snapshot.
    """
    try:
        def boot_id() -> str:
            value = _compact_hex(_proc_bytes(Path("/proc/sys/kernel/random/boot_id"), 64).decode().removesuffix("\n"), size=32)
            if value is None:
                raise ValueError
            return value

        def children(owner: int) -> set[int]:
            result: set[int] = set()
            with os.scandir(f"/proc/{owner}/task") as tasks:
                for index, task in enumerate(tasks):
                    if index >= 128:
                        raise OverflowError
                    for child in _proc_bytes(Path(task.path) / "children", 4096).split():
                        if not child.isdigit() or not 1 < int(child) <= 2_147_483_647:
                            raise ValueError
                        result.add(int(child))
                        if len(result) > 8:
                            raise OverflowError
            return result

        def sockets(owner: int) -> set[int]:
            inodes: set[int] = set()
            with os.scandir(f"/proc/{owner}/fd") as descriptors:
                for index, descriptor in enumerate(descriptors):
                    if index >= 512:
                        raise OverflowError
                    target = os.readlink(descriptor.path)
                    match = re.fullmatch(r"socket:\[([1-9][0-9]*)\]", target)
                    if match:
                        inodes.add(int(match[1]))
            return inodes

        boot = boot_id()
        identities = {pid: _proc_identity(pid)}
        credential_mode = _connector_credential_mode(pid)
        child_sets: dict[int, set[int]] = {}
        pending = [(pid, 0)]
        while pending:
            owner, depth = pending.pop()
            found = children(owner)
            child_sets[owner] = found
            if depth >= 4 and found:
                raise OverflowError
            for child in sorted(found):
                if child in identities or len(identities) >= 9:
                    raise OverflowError
                identity = _proc_identity(child)
                if identity[0] != owner or identity[2] != identities[pid][2]:
                    return {"loss": "attribution_loss"}
                identities[child] = identity
                pending.append((child, depth + 1))
        if len(identities) == 1:
            return {"loss": "unavailable_child"}
        socket_sets: dict[int, set[int]] = {}
        listener_pids: list[int] = []
        for owner in identities:
            # Include the actual onefile Python worker, not just its launcher.
            socket_sets[owner] = sockets(owner)
            raw = _proc_bytes(Path(f"/proc/{owner}/net/tcp"), 262144)
            lines = raw.splitlines()
            if not lines or b"local_address" not in lines[0] or b"inode" not in lines[0]:
                raise ValueError
            for line in lines[1:]:
                fields = line.split()
                if len(fields) < 10 or not fields[9].isdigit() or not re.fullmatch(rb"[0-9A-F]{8}:[0-9A-F]{4}", fields[1]) or not re.fullmatch(rb"[0-9A-F]{2}", fields[3]):
                    raise ValueError
                # Only the harness-configured literal IPv4 loopback endpoint.
                if fields[1] == b"0100007F:480B" and fields[3] == b"0A" and int(fields[9]) in socket_sets[owner]:
                    listener_pids.append(owner)
        for owner, identity in identities.items():
            if identity != _proc_identity(owner) or children(owner) != child_sets[owner] or sockets(owner) != socket_sets[owner]:
                return {"loss": "attribution_loss"}
        if boot != boot_id() or len(listener_pids) > 1:
            return {"loss": "attribution_loss"}
        listening = bool(listener_pids)
        return {"loss": "observed", "boot_id": boot, "main_pid": pid,
                "processes": [{"pid": owner, "parent_pid": identity[0], "start_ticks": identity[1]} for owner, identity in sorted(identities.items())],
                "credential_mode": credential_mode,
                "listener_pid": listener_pids[0] if listening else None,
                "listening": listening, "bound_port": 18443 if listening else None}
    except OverflowError:
        return {"loss": "truncated"}
    except FileNotFoundError:
        return {"loss": "unavailable_process"}
    except OSError:
        return {"loss": "query_error"}
    except (UnicodeError, ValueError):
        return {"loss": "parse_loss"}


def _connector_process_sample(raw: bytes, pid: int) -> dict[str, Any]:
    """Validate each closed worker projection before retaining or comparing it."""
    process = _closed_json(raw.decode("ascii"))
    if not isinstance(process, dict):
        raise ValueError
    if set(process) == {"loss"} and process["loss"] in {"truncated", "unavailable_process", "unavailable_child", "attribution_loss", "parse_loss", "query_error"}:
        return process
    if set(process) != {"loss", "boot_id", "main_pid", "processes", "listener_pid", "listening", "bound_port", "credential_mode"}:
        raise ValueError
    if process["loss"] != "observed" or not isinstance(process["boot_id"], str) or _compact_hex(process["boot_id"], size=32) != process["boot_id"] or type(process["listening"]) is not bool:
        raise ValueError
    _credential_mode_sample(process["credential_mode"])
    rows = process["processes"]
    if not isinstance(rows, list) or not 2 <= len(rows) <= 9:
        raise ValueError
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"pid", "parent_pid", "start_ticks"} or any(type(value) is not int or value <= 0 for value in row.values()):
            raise ValueError
    pids = {row["pid"] for row in rows}
    if len(pids) != len(rows) or pid not in pids or any(row["pid"] != pid and row["parent_pid"] not in pids for row in rows):
        raise ValueError
    if process["listening"]:
        if type(process["listener_pid"]) is not int or process["listener_pid"] not in pids:
            raise ValueError
        if type(process["bound_port"]) is not int or process["bound_port"] != 18443:
            raise ValueError
    elif process["listener_pid"] is not None or process["bound_port"] is not None:
        raise ValueError
    if type(process["main_pid"]) is not int or process["main_pid"] != pid:
        raise ValueError
    return process


def capture_connector() -> dict[str, Any]:
    # Four observations: 1s query, 2s process, 1s query, 2s process,
    # each with at most 1s group reaping, within the reduced 6s live section;
    # journal capture owns the rest of the existing connector section. Partial
    # live sampling refuses rather than borrowing that reserved journal time;
    # launch only when both this stage and its cleanup fit the shared deadline.
    deadline = time.monotonic() + 6
    worker = [sys.executable, str(Path(__file__).resolve())]
    query = ["systemctl", "show", CONNECTOR_UNIT] + [f"--property={name}" for name in sorted(CONNECTOR_PROPERTIES)]

    def observe(argv: list[str], seconds: float, cap: int) -> tuple[bytes, str]:
        if time.monotonic() + seconds + 1 >= deadline:
            return b"", "timeout"
        return _bounded_observation(argv, seconds=seconds, cap=cap)

    raw, loss = observe(query, 1, 4096)
    if loss != "observed":
        return _connector_unknown(loss)
    try:
        before = _connector_properties(raw)
    except (UnicodeError, ValueError):
        return _connector_unknown("parse_loss")
    pid = int(before["MainPID"])
    if pid == 0:
        return _connector_unknown("failed" if before["ActiveState"] == "failed" else "absent")
    raw, loss = observe(worker + ["--mode", "connector-process", "--connector-pid", str(pid)], 2, 2048)
    if loss != "observed":
        return _connector_unknown(loss)
    try:
        process = _connector_process_sample(raw, pid)
        if process["loss"] != "observed":
            return _connector_unknown(process["loss"])
    except (UnicodeError, ValueError, TypeError, RecursionError):
        return _connector_unknown("parse_loss")
    raw, loss = observe(query, 1, 4096)
    if loss != "observed":
        return _connector_unknown(loss)
    try:
        after = _connector_properties(raw)
    except (UnicodeError, ValueError):
        return _connector_unknown("parse_loss")
    if before != after or time.monotonic() >= deadline:
        return _connector_unknown("attribution_loss" if before != after else "timeout")
    repeated, loss = observe(worker + ["--mode", "connector-process", "--connector-pid", str(pid)], 2, 2048)
    if loss != "observed":
        return _connector_unknown(loss)
    try:
        confirmed = _connector_process_sample(repeated, pid)
        if confirmed["loss"] != "observed":
            return _connector_unknown(confirmed["loss"])
        # Observation times differ; every other closed field must agree.
        if ({key: value for key, value in confirmed.items() if key != "credential_mode"} !=
                {key: value for key, value in process.items() if key != "credential_mode"} or
                {key: value for key, value in confirmed["credential_mode"].items() if key != "observed_at_us"} !=
                {key: value for key, value in process["credential_mode"].items() if key != "observed_at_us"}):
            return _connector_unknown("attribution_loss")
        first_time, last_time = process["credential_mode"]["observed_at_us"], confirmed["credential_mode"]["observed_at_us"]
        if first_time is not None and (last_time < first_time or last_time > time.time_ns() // 1000):
            return _connector_unknown("attribution_loss")
    except (UnicodeError, ValueError, TypeError, RecursionError):
        return _connector_unknown("parse_loss")
    if time.monotonic() >= deadline:
        return _connector_unknown("timeout")
    result = _connector_unknown("observed")
    result.update(binding={key: process[key] for key in ("boot_id", "main_pid", "processes", "listener_pid")},
                  listening=process["listening"], bound_port=process["bound_port"],
                  credential_mode=process["credential_mode"],
                  status=CONNECTOR_STATUS.get(before["StatusText"], "unclassified"))
    result["binding"]["invocation_id"] = _compact_hex(before["InvocationID"], size=32)
    return result


# Closed counterpart of connector_supervisor.go's fixed diagnostic call sites.
CONNECTOR_REASONS = frozenset({
    "invalid_invocation", "notifier_setup_failed", "generation_setup_failed",
    "health_pipe_setup_failed", "child_start_failed", "context_cancelled",
    "startup_expired", "freshness_expired", "health_read_failed", "health_malformed",
    "health_noncanonical", "health_partial", "health_version_rejected",
    "health_generation_rejected", "health_sequence_rejected", "readiness_regressed",
    "ready_state_rejected", "healthy_state_rejected", "ready_notify_failed",
    "watchdog_notify_failed", "child_reported_stopping", "child_reported_failed",
    "health_state_unknown", "sidecar_unknown", "sidecar_absent", "sidecar_unhealthy",
    "child_exit_error", "child_exit_after_stop", "child_exit_unexpected_clean",
    "scanner_join_timeout", "stopping_notify_failed", "admission_removal_refused",
    "child_term_failed", "health_pipe_closed", "invocation_started",
    "admission_initial_unknown", "admission_stop_request_failed", "admission_cancelled",
    "admission_expired", "admission_poll_unknown",
})
CONNECTOR_LABEL = re.compile(
    r"connector_supervisor phase=(context|observation|first|consequence) reason=([a-z_]+) "
    r"generation=([0-9a-f]{32}|unavailable) sequence=(0|[1-9][0-9]{0,19})"
)



# Version2 is a separate grammar and retention budget. It never supplies a v1
# Stop receipt, readiness proof, or an override of the successful-dial veto.
RECOVERY_V2_PREFIX = "connector_recovery_v2 "
RECOVERY_V2_KEYS = frozenset({
    "version", "phase", "reason", "origin", "outcome", "exit",
    "policy_revision", "generation", "sequence", "rate_count",
})
RECOVERY_V2_PHASES = {
    "context": frozenset({"invocation_started", "publication_observed", "boot_validated", "boot_refused"}),
    "decision": frozenset({"intentional_stop", "sidecar_loss", "child_loss", "health_invalid",
        "startup_expired", "freshness_expired", "watchdog_failed", "state_invalid",
        "state_io_failed", "transaction_pending", "lock_unavailable", "identity_lost"}),
    "withdrawal": frozenset({"admission_absent", "admission_unknown"}),
    "cleanup": frozenset({"join_failed", "cleanup_blocked"}),
    "readmission": frozenset({"fresh_listener_recovered", "healthy_reset"}),
    "terminal": frozenset({"intentional_stop", "restart_requested", "cleanup_blocked",
        "manager_refused", "rate_deferred", "rate_window_exhausted"}),
}


def _recovery_v2_record(message: str) -> dict[str, Any]:
    if not message.startswith(RECOVERY_V2_PREFIX) or len(message.encode("utf-8")) > 512:
        raise ValueError("recovery grammar")
    encoded = message.removeprefix(RECOVERY_V2_PREFIX)
    raw = _closed_json(encoded)
    if (not isinstance(raw, dict) or set(raw) != RECOVERY_V2_KEYS
            or json.dumps(raw, sort_keys=True, separators=(",", ":")) != encoded
            or type(raw["version"]) is not int or raw["version"] != 2
            or not isinstance(raw["phase"], str) or raw["phase"] not in RECOVERY_V2_PHASES
            or not isinstance(raw["reason"], str) or raw["reason"] not in RECOVERY_V2_PHASES[raw["phase"]]
            or raw["origin"] not in ("intentional_request", "crash_observed", "unknown")
            or raw["outcome"] not in ("pending", "stopped", "restart_requested", "fresh_listener_recovered", "deferred", "blocked")
            or raw["exit"] is not None and (type(raw["exit"]) is not int or raw["exit"] not in (0, 1, 78))
            or raw["policy_revision"] is not None and (type(raw["policy_revision"]) is not int or not 1 <= raw["policy_revision"] <= 2**63 - 1)
            or raw["generation"] is not None and (not isinstance(raw["generation"], str) or re.fullmatch(r"[0-9a-f]{32}", raw["generation"]) is None or raw["generation"] == "0" * 32)
            or type(raw["sequence"]) is not int or not 0 <= raw["sequence"] <= 2**64 - 1
            or type(raw["rate_count"]) is not int or not 0 <= raw["rate_count"] <= 3):
        raise ValueError("recovery schema")
    phase, reason, outcome, code = raw["phase"], raw["reason"], raw["outcome"], raw["exit"]
    if raw["policy_revision"] is None and raw["generation"] is not None:
        raise ValueError("recovery context")
    if raw["generation"] is None and raw["sequence"] != 0:
        raise ValueError("recovery sequence")
    if phase != "terminal" and code is not None:
        raise ValueError("recovery nonterminal")
    if reason == "publication_observed" and (raw["origin"] != "unknown" or outcome != "pending" or code is not None):
        raise ValueError("publication is not acknowledgement")
    if reason == "boot_validated" and (outcome != "pending" or code is not None):
        raise ValueError("boot is not readiness")
    if reason in {"rate_deferred", "rate_window_exhausted"} and (outcome != "deferred" or code != 1):
        raise ValueError("rate is not cleanup")
    if reason == "intentional_stop" and phase == "terminal" and (outcome != "stopped" or code != 0 or raw["origin"] != "intentional_request"):
        raise ValueError("stopped terminal")
    if reason == "restart_requested" and (outcome != "restart_requested" or code != 1):
        raise ValueError("restart terminal")
    if reason in {"cleanup_blocked", "manager_refused"} and phase == "terminal" and (outcome != "blocked" or code != 78):
        raise ValueError("blocked terminal")
    if reason == "fresh_listener_recovered" and outcome != "fresh_listener_recovered":
        raise ValueError("recovery is not request")
    if reason == "healthy_reset" and (outcome != "pending" or raw["rate_count"] != 0):
        raise ValueError("reset publication")
    return raw


def _recovery_v2_projection(event: dict[str, Any], boot: str, since: int, until: int,
                            state: dict[str, Any]) -> None:
    raw = _recovery_v2_record(event["MESSAGE"])
    invocation, pid = event.get("_SYSTEMD_INVOCATION_ID"), event.get("_PID")
    timestamp, monotonic, cursor = event.get("__REALTIME_TIMESTAMP"), event.get("__MONOTONIC_TIMESTAMP"), event.get("__CURSOR")
    if (event.get("_SYSTEMD_UNIT") != CONNECTOR_UNIT or event.get("_BOOT_ID") != boot
            or not isinstance(invocation, str) or re.fullmatch(r"[0-9a-f]{32}", invocation) is None or invocation == "0" * 32
            or not isinstance(pid, str) or re.fullmatch(r"[1-9][0-9]{0,9}", pid) is None or not 1 < int(pid) <= 2**31 - 1
            or any(not isinstance(value, str) or re.fullmatch(r"[1-9][0-9]{0,17}", value) is None for value in (timestamp, monotonic))
            or not since <= int(timestamp) <= until
            or not isinstance(cursor, str) or not 0 < len(cursor) <= 512 or not cursor.isascii() or not cursor.isprintable()):
        state["losses"].add("attribution_loss")
        return
    previous = state["identities"].get(invocation)
    current = (pid, raw["generation"], raw["sequence"], int(monotonic), raw["policy_revision"])
    # A live invocation retains its actual admission-policy revision. The
    # closed producer may project a newer policy ONLY at its final proven
    # stopped inhibition, after the same generation's physical cleanup.
    stopped_revision = (previous is not None and previous[4] is not None
        and raw["policy_revision"] is not None and raw["policy_revision"] > previous[4]
        and previous[1] is not None and previous[1] == raw["generation"]
        and raw["phase"] == "terminal" and raw["reason"] == "intentional_stop"
        and raw["origin"] == "intentional_request" and raw["outcome"] == "stopped" and raw["exit"] == 0)
    terminal_seen = any(key[0] == invocation and key[1] == "terminal"
        for key in state["seen"])
    if (cursor in state["cursors"] or terminal_seen or previous is not None and (
            previous[0] != pid or previous[1] is not None and previous[1] != raw["generation"]
            or raw["sequence"] < previous[2] or int(monotonic) < previous[3]
            or previous[4] is not None and previous[4] != raw["policy_revision"] and not stopped_revision)):
        state["losses"].add("attribution_loss")
        return
    if raw["phase"] == "decision" and invocation in state["decisions"]:
        raise ValueError("duplicate recovery decision")
    key = (invocation, raw["phase"], raw["reason"])
    if key in state["seen"]:
        raise ValueError("duplicate recovery consequence")
    state["seen"].add(key)
    state["cursors"].add(cursor)
    state["identities"][invocation] = current
    if raw["phase"] == "decision":
        state["decisions"].add(invocation)
    projected = dict(raw, invocation=invocation, pid=int(pid), realtime_us=int(timestamp), monotonic_us=int(monotonic))
    # Attribution/cursor/decision/terminal state above keeps advancing even
    # after this grammar's first capacity refusal; no holes or later refill.
    if state["retention_closed"]:
        return
    if not _connector_journal_fits(state["legacy_events"], state["events"] + [projected]):
        state["retention_closed"] = True
        state["losses"].add("truncated")
        return
    state["events"].append(projected)


# One delivered UTF8+LF envelope, with independently latched whole prefixes.
# These are ceilings, not promised history or terminal-event retention.
_CONNECTOR_JOURNAL_CAP = 2048
_CONNECTOR_LEGACY_ARRAY_CAP = 512
_CONNECTOR_RECOVERY_ARRAY_CAP = 896
_CONNECTOR_METADATA_CAP = 640
_CONNECTOR_LEGACY_LOSSES = frozenset({
    "unattempted", "observed", "empty", "attribution_loss", "truncated",
    "parse_loss", "query_error", "timeout", "reap_unconfirmed", "launch_failure",
})
_CONNECTOR_RECOVERY_LOSSES = frozenset({
    "unavailable", "attribution_loss", "truncated", "parse_loss", "query_error",
    "timeout", "reap_unconfirmed",
})


def _connector_journal_json(raw: str) -> Any:
    return _closed_json(raw)


def _connector_journal_empty(loss: str) -> dict[str, Any]:
    # Never substitute a current activation for the unavailable original pin.
    recovery_loss = "unavailable" if loss in {"unattempted", "launch_failure"} else loss
    return {"unit": CONNECTOR_UNIT, "events": [], "original_invocation": None,
            "attribution": "window_only", "losses": [loss], "tail_complete": False,
            "recovery_v2": {"events": [], "losses": sorted({"unavailable", recovery_loss}),
                            "tail_complete": False}}


def _connector_journal_bytes(value: Any) -> bytes:
    return (json.dumps(value, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8", "strict")


def _connector_journal_fits(legacy: list[dict[str, Any]], recovery: list[dict[str, Any]]) -> bool:
    # Admission checks a complete candidate with the longest possible losses.
    # Local array reservations cannot borrow the other grammar's unused space.
    candidate = _connector_journal_empty("unattempted")
    candidate.update(events=legacy, losses=["attribution_loss", "truncated", "parse_loss",
        "query_error", "timeout", "reap_unconfirmed", "launch_failure"])
    candidate["recovery_v2"].update(events=recovery, losses=["unavailable", "attribution_loss",
        "truncated", "parse_loss", "query_error", "timeout", "reap_unconfirmed"])
    legacy_bytes = len(_connector_journal_bytes(legacy)) - 1
    recovery_bytes = len(_connector_journal_bytes(recovery)) - 1
    whole_bytes = len(_connector_journal_bytes(candidate))
    metadata_bytes = whole_bytes - legacy_bytes - recovery_bytes
    return (len(legacy) <= 6 and len(recovery) <= 16
            and legacy_bytes <= _CONNECTOR_LEGACY_ARRAY_CAP
            and recovery_bytes <= _CONNECTOR_RECOVERY_ARRAY_CAP
            and metadata_bytes <= _CONNECTOR_METADATA_CAP
            and whole_bytes <= _CONNECTOR_JOURNAL_CAP)


def _connector_journal_delivery(raw: bytes) -> str:
    """Validate the entire closed delivery BEFORE shell substitution strips LF/NUL."""
    if (not 0 < len(raw) <= _CONNECTOR_JOURNAL_CAP or not raw.endswith(b"\n")
            or b"\x00" in raw or b"\n" in raw[:-1] or b"\r" in raw):
        raise ValueError("journal framing")
    text = raw.decode("utf-8", "strict")
    value = _connector_journal_json(text)
    if (not isinstance(value, dict) or set(value) != {
            "unit", "events", "original_invocation", "attribution", "losses", "tail_complete", "recovery_v2"}
            or value["unit"] != CONNECTOR_UNIT or value["original_invocation"] is not None
            or value["attribution"] != "window_only"):
        raise ValueError("journal envelope")
    recovery = value["recovery_v2"]
    if not isinstance(recovery, dict) or set(recovery) != {"events", "losses", "tail_complete"}:
        raise ValueError("recovery envelope")
    for projection, vocabulary in ((value, _CONNECTOR_LEGACY_LOSSES), (recovery, _CONNECTOR_RECOVERY_LOSSES)):
        events, losses = projection["events"], projection["losses"]
        if (type(events) is not list or type(losses) is not list
                or any(type(loss) is not str or loss not in vocabulary for loss in losses)
                or len(losses) != len(set(losses)) or type(projection["tail_complete"]) is not bool
                or projection["tail_complete"] and any(loss not in {"observed", "empty", "unavailable"} for loss in losses)):
            raise ValueError("journal projection")
    legacy_keys = {"invocation", "pid", "realtime_us", "monotonic_us", "phase", "reason", "generation", "sequence"}
    context_keys = {"invocation", "pid", "realtime_us", "monotonic_us"}
    for rows, keys in ((value["events"], legacy_keys), (recovery["events"], RECOVERY_V2_KEYS | context_keys)):
        for row in rows:
            if (not isinstance(row, dict) or set(row) != keys
                    or not isinstance(row["invocation"], str)
                    or re.fullmatch(r"[0-9a-f]{32}", row["invocation"]) is None or row["invocation"] == "0" * 32
                    or type(row["pid"]) is not int or not 1 < row["pid"] <= 2**31 - 1
                    or any(type(row[key]) is not int or not 0 < row[key] < 10**18 for key in ("realtime_us", "monotonic_us"))):
                raise ValueError("journal event")
            if keys == legacy_keys:
                if (not isinstance(row["phase"], str) or row["phase"] not in {"context", "observation", "first", "consequence"}
                        or not isinstance(row["reason"], str) or row["reason"] not in CONNECTOR_REASONS
                        or type(row["sequence"]) is not int or not 0 <= row["sequence"] <= 2**64 - 1
                        or row["generation"] is not None and (not isinstance(row["generation"], str)
                            or re.fullmatch(r"[0-9a-f]{32}", row["generation"]) is None)):
                    raise ValueError("legacy event")
            else:
                message = RECOVERY_V2_PREFIX + json.dumps({key: row[key] for key in RECOVERY_V2_KEYS}, sort_keys=True, separators=(",", ":"), allow_nan=False)
                _recovery_v2_record(message)
    if not _connector_journal_fits(value["events"], recovery["events"]) or _connector_journal_bytes(value) != raw:
        raise ValueError("journal size or compact framing")
    return text


def _emit_connector_journal(value: dict[str, Any]) -> bool:
    # The fallback is itself checked; no oversized or empty-success delivery.
    fallback = False
    try:
        raw = _connector_journal_bytes(value)
        if len(raw) > _CONNECTOR_JOURNAL_CAP or not _connector_journal_fits(value["events"], value["recovery_v2"]["events"]):
            value = _connector_journal_empty("truncated")
            raw = _connector_journal_bytes(value)
            fallback = True
        text = _connector_journal_delivery(raw)
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raw = _connector_journal_bytes(_connector_journal_empty("parse_loss"))
        text = _connector_journal_delivery(raw)
        fallback = True
    sys.stdout.write(text)
    sys.stdout.flush()
    return not fallback


class _ConnectorJournalCancelled(Exception):
    def __init__(self, value: dict[str, Any], status: int):
        self.value = value
        self.status = status
        super().__init__("journal cancelled")


def capture_connector_journal(boot: str | None, since: int | None, until: int | None) -> dict[str, Any]:
    """Stream/filter before retention; preserve partial evidence and lost tail."""
    result = _connector_journal_empty("unattempted")
    if (not isinstance(boot, str) or re.fullmatch(r"[0-9a-f]{32}", boot) is None
            or boot == "0" * 32 or since is None or until is None
            or not 0 < since <= until < 10**18):
        result["losses"] = ["attribution_loss"]
        result["recovery_v2"]["losses"] = ["attribution_loss"]
        return result
    deadline = time.monotonic() + 3
    losses: set[str] = set()
    shared_losses: set[str] = set()
    retention_closed = False
    eof = query_ok = reaped = False
    pending = bytearray()
    total = frames = 0
    cursors: set[str] = set()
    identities: dict[str, tuple[str, str, int, int]] = {}
    firsts: set[str] = set()
    events = result["events"]
    recovery = {"events": [], "losses": set(), "identities": {}, "cursors": set(),
                "decisions": set(), "seen": set(), "retention_closed": False, "legacy_events": events}

    def project(frame: bytes) -> None:
        nonlocal frames, retention_closed
        frames += 1
        if frames > 256:
            shared_losses.add("truncated")
            return
        try:
            event = _connector_journal_json(frame.decode("utf-8", "strict"))
            if not isinstance(event, dict):
                raise ValueError("shape")
            message = event.get("MESSAGE")
            if not isinstance(message, str):
                raise ValueError("message")
            if message.startswith(RECOVERY_V2_PREFIX):
                try:
                    _recovery_v2_projection(event, boot, since, until, recovery)
                except (ValueError, TypeError, UnicodeError, RecursionError):
                    recovery["losses"].add("parse_loss")
                return
            if not message.startswith("connector_supervisor "):
                return
            matched = CONNECTOR_LABEL.fullmatch(message)
            if matched is None:
                raise ValueError("grammar")
            phase, reason, generation, sequence_text = matched.groups()
            sequence = int(sequence_text)
            if reason not in CONNECTOR_REASONS or sequence > 2**64 - 1:
                raise ValueError("reason")
            observation_reasons = {"sidecar_unknown", "sidecar_absent", "sidecar_unhealthy", "health_pipe_closed"}
            consequence_only = {"scanner_join_timeout", "stopping_notify_failed", "admission_removal_refused",
                                "child_term_failed", "health_pipe_closed", "admission_initial_unknown",
                                "admission_stop_request_failed", "admission_cancelled", "admission_expired",
                                "admission_poll_unknown"}
            if ((phase == "context") != (reason == "invocation_started")
                    or phase == "observation" and reason not in observation_reasons
                    or phase == "first" and reason in consequence_only):
                raise ValueError("phase")
            if generation == "unavailable" and (sequence != 0 or reason not in {
                    "invalid_invocation", "notifier_setup_failed", "generation_setup_failed"}):
                raise ValueError("generation")
            invocation, pid = event.get("_SYSTEMD_INVOCATION_ID"), event.get("_PID")
            timestamp, monotonic = event.get("__REALTIME_TIMESTAMP"), event.get("__MONOTONIC_TIMESTAMP")
            cursor = event.get("__CURSOR")
            if (not isinstance(invocation, str) or re.fullmatch(r"[0-9a-f]{32}", invocation) is None
                    or invocation == "0" * 32 or not isinstance(pid, str)
                    or re.fullmatch(r"[1-9][0-9]{0,9}", pid) is None or not 1 < int(pid) <= 2_147_483_647
                    or event.get("_SYSTEMD_UNIT") != CONNECTOR_UNIT or event.get("_BOOT_ID") != boot):
                losses.add("attribution_loss")
                return
            if any(not isinstance(value, str) or re.fullmatch(r"[1-9][0-9]{0,17}", value) is None
                   for value in (timestamp, monotonic)):
                raise ValueError("time")
            if not since <= int(timestamp) <= until:
                losses.add("attribution_loss")
                return
            if (not isinstance(cursor, str) or not 0 < len(cursor) <= 512
                    or not cursor.isascii() or not cursor.isprintable() or cursor in cursors):
                raise ValueError("cursor")
            cursors.add(cursor)
            previous = identities.get(invocation)
            if previous is not None and (previous[:2] != (pid, generation)
                                         or sequence < previous[2] or int(monotonic) < previous[3]):
                losses.add("attribution_loss")
                return
            if phase == "first" and invocation in firsts:
                raise ValueError("duplicate first")
            if phase == "first":
                firsts.add(invocation)
            identities[invocation] = (pid, generation, sequence, int(monotonic))
            projected = {"invocation": invocation, "pid": int(pid), "realtime_us": int(timestamp),
                         "monotonic_us": int(monotonic), "phase": phase, "reason": reason,
                         "generation": None if generation == "unavailable" else generation, "sequence": sequence}
            if retention_closed:
                return
            if not _connector_journal_fits(events + [projected], recovery["events"]):
                retention_closed = True
                losses.add("truncated")
                return
            events.append(projected)
        except (ValueError, TypeError, UnicodeError, RecursionError):
            # Without a safely identified v2 record, malformed framing/JSON/
            # shape is shared loss. Identifiable v2 projection errors returned
            # above after recording only that grammar's loss.
            shared_losses.add("parse_loss")

    argv = ["journalctl", "-u", CONNECTOR_UNIT, "-b", boot,
            "--since", f"@{since // 1000000}.{since % 1000000:06d}",
            "--until", f"@{until // 1000000}.{until % 1000000:06d}",
            "--output=json", "--all", "--no-pager",
            "--output-fields=MESSAGE,_SYSTEMD_UNIT,_BOOT_ID,_SYSTEMD_INVOCATION_ID,_PID,__REALTIME_TIMESTAMP,__MONOTONIC_TIMESTAMP,__CURSOR",
            "--grep=^(connector_supervisor |connector_recovery_v2 )"]
    process = None
    cancelled = 0
    signals = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
    handlers = {item: signal.getsignal(item) for item in signals}

    def defer_cancel(signum: int, _frame: Any) -> None:
        nonlocal cancelled
        # Neither launch-handle acquisition nor entry into/finalization of
        # cleanup may be unwound by a first or repeated catchable signal.
        cancelled = cancelled or signum
        # Propagate to the enclosing nonthrowing capture/delivery owner so
        # its first delivered signal survives both nested handler lifetimes.
        previous = handlers[signum]
        if callable(previous) and previous is not signal.default_int_handler:
            try:
                previous(signum, _frame)
            except BaseException:
                # Inherited handlers cannot unwind handle acquisition/reaping.
                pass

    def read() -> None:
        nonlocal total, pending, eof, query_ok
        assert process is not None
        assert process.stdout is not None
        with selectors.DefaultSelector() as selector:
            os.set_blocking(process.stdout.fileno(), False)
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                if cancelled:
                    shared_losses.add("timeout")
                    return
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    shared_losses.add("timeout")
                    break
                for key, _ in selector.select(min(remaining, 0.05)):
                    if cancelled:
                        shared_losses.add("timeout")
                        return
                    if time.monotonic() >= deadline:
                        shared_losses.add("timeout")
                        break
                    chunk = os.read(key.fd, min(4096, 65537 - total))
                    if not chunk:
                        selector.unregister(key.fileobj)
                        eof = True
                        break
                    total += len(chunk)
                    if total > 65536:
                        shared_losses.add("truncated")
                        break
                    pending.extend(chunk)
                    while b"\n" in pending:
                        frame, _, rest = pending.partition(b"\n")
                        pending = bytearray(rest)
                        if len(frame) > 8192:
                            shared_losses.add("truncated")
                            break
                        project(frame)
                        if frames >= 256 or "truncated" in shared_losses:
                            shared_losses.add("truncated")
                            break
                    if len(pending) > 8192 or frames >= 256:
                        shared_losses.add("truncated")
                if "timeout" in shared_losses or "truncated" in shared_losses:
                    break
            if not selector.get_map():
                if pending:
                    shared_losses.add("parse_loss")
                status = None
                while True:
                    if cancelled:
                        shared_losses.add("timeout")
                        return
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        shared_losses.add("timeout")
                        break
                    try:
                        status = process.wait(timeout=min(remaining, 0.05))
                        # grep's no-match exit1 is not independently trusted as
                        # empty: query failure must remain explicit.
                        if status != 0:
                            shared_losses.add("query_error")
                        if time.monotonic() >= deadline:
                            shared_losses.add("timeout")
                        break
                    except subprocess.TimeoutExpired:
                        continue
                query_ok = status == 0 and time.monotonic() < deadline

    try:
        for item in signals:
            signal.signal(item, defer_cancel)
        if not cancelled:
            try:
                process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                           stderr=subprocess.DEVNULL, start_new_session=True)
            except OSError:
                shared_losses.add("launch_failure")
        if process is not None and not cancelled:
            read()
    except (OSError, ValueError):
        shared_losses.add("query_error")
    finally:
        try:
            try:
                if process is not None:
                    reaped = _terminate_observation(process)
                    if not reaped:
                        shared_losses.add("reap_unconfirmed")
            except OSError:
                shared_losses.add("reap_unconfirmed")
            finally:
                if process is not None and process.stdout is not None:
                    try:
                        process.stdout.close()
                    except OSError:
                        reaped = False
                        shared_losses.add("reap_unconfirmed")
        finally:
            for item, handler in handlers.items():
                signal.signal(item, handler)
    if cancelled:
        shared_losses.add("timeout")
    # Neither a local cap nor the other grammar's loss alters this tail.
    # Actual original EOF/query success/confirmed group reap are all required.
    complete = eof and query_ok and reaped and not shared_losses
    legacy_losses = losses | shared_losses
    recovery_losses = recovery["losses"] | {
        "unavailable" if loss == "launch_failure" else loss for loss in shared_losses
    }
    result["losses"] = sorted(legacy_losses) or ["observed" if events else "empty"]
    result["tail_complete"] = complete and not losses
    result["recovery_v2"] = {
        "events": recovery["events"],
        "losses": sorted(recovery_losses) or ([] if recovery["events"] else ["unavailable"]),
        "tail_complete": complete and not recovery["losses"],
    }
    if cancelled:
        raise _ConnectorJournalCancelled(result, 128 + cancelled)
    return result


def hold_negative_unit_reference(boot: str | None, run: str | None) -> int:
    """Hold ONE private observation reference; never reconnect or start a unit."""
    if (boot is None or re.fullmatch(r"[0-9a-f]{32}", boot) is None
            or run is None or re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", run) is None):
        return 1
    bus = ctypes.c_void_p()
    library = None
    try:
        # libsystemd0 is part of the Ubuntu/systemd venue, not an installed
        # Python dependency. Missing ABI/library refuses; no fallback client.
        library = ctypes.CDLL("libsystemd.so.0")
        pointer, string = ctypes.c_void_p, ctypes.c_char_p
        signatures = {
            "sd_bus_open_system": ([ctypes.POINTER(pointer)], ctypes.c_int),
            "sd_bus_set_method_call_timeout": ([pointer, ctypes.c_uint64], ctypes.c_int),
            "sd_bus_get_unique_name": ([pointer, ctypes.POINTER(string)], ctypes.c_int),
            "sd_bus_call_method": ([pointer, string, string, string, string, pointer, ctypes.POINTER(pointer), string], ctypes.c_int),
            "sd_bus_message_get_sender": ([pointer], string),
            "sd_bus_message_read_basic": ([pointer, ctypes.c_char, pointer], ctypes.c_int),
            "sd_bus_get_property": ([pointer, string, string, string, string, pointer, ctypes.POINTER(pointer), string], ctypes.c_int),
            "sd_bus_message_enter_container": ([pointer, ctypes.c_char, string], ctypes.c_int),
            "sd_bus_message_exit_container": ([pointer], ctypes.c_int),
            "sd_bus_message_at_end": ([pointer, ctypes.c_int], ctypes.c_int),
            "sd_bus_message_unref": ([pointer], pointer),
            "sd_bus_close": ([pointer], None),
            "sd_bus_unref": ([pointer], pointer),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(library, name)
            function.argtypes, function.restype = arguments, result

        def require(value: int) -> None:
            if value < 0:
                raise ValueError("reference query failed")

        def current_boot() -> None:
            if Path("/proc/sys/kernel/random/boot_id").read_text().strip().replace("-", "") != boot:
                raise ValueError("changed boot")

        manager = b"org.freedesktop.systemd1"
        root = b"/org/freedesktop/systemd1"
        interface = b"org.freedesktop.systemd1.Manager"
        unit = SIDECAR_UNIT.encode("ascii")
        deadline = time.monotonic() + 120
        current_boot()
        require(library.sd_bus_open_system(ctypes.byref(bus)))
        require(library.sd_bus_set_method_call_timeout(bus, 2_000_000))
        own = string()
        require(library.sd_bus_get_unique_name(bus, ctypes.byref(own)))
        if own.value is None:
            raise ValueError("missing client")
        own_name = own.value

        def call(destination: bytes, method: bytes) -> tuple[bytes | None, bytes | None]:
            reply = pointer()
            try:
                require(library.sd_bus_call_method(bus, destination, root, interface, method,
                                                  None, ctypes.byref(reply), b"s", string(unit)))
                sender = library.sd_bus_message_get_sender(reply)
                path = string()
                if method == b"GetUnit":
                    if library.sd_bus_message_read_basic(reply, b"o", ctypes.byref(path)) != 1:
                        raise ValueError("missing object")
                    if library.sd_bus_message_at_end(reply, 1) != 1:
                        raise ValueError("extra object data")
                return sender, path.value
            finally:
                library.sd_bus_message_unref(reply)

        owner, _ = call(manager, b"RefUnit")
        if owner is None or re.fullmatch(rb":[0-9]+\.[0-9]+", owner) is None:
            raise ValueError("missing owner")
        sender, path = call(owner, b"GetUnit")
        if sender != owner or path != b"/org/freedesktop/systemd1/unit/happyranch_2dtsnet_2dsidecar_2eservice":
            raise ValueError("changed object")

        def held() -> None:
            current_boot()
            if time.monotonic() >= deadline:
                raise ValueError("expired reference")
            reply = pointer()
            try:
                require(library.sd_bus_get_property(bus, owner, path, b"org.freedesktop.systemd1.Unit",
                                                   b"Refs", None, ctypes.byref(reply), b"as"))
                if (library.sd_bus_message_get_sender(reply) != owner
                        or library.sd_bus_message_enter_container(reply, b"a", b"s") != 1):
                    raise ValueError("changed owner")
                matches = 0
                for _ in range(65):
                    value = string()
                    result = library.sd_bus_message_read_basic(reply, b"s", ctypes.byref(value))
                    require(result)
                    if result == 0:
                        break
                    matches += value.value == own_name
                else:
                    raise ValueError("oversize references")
                if (matches != 1 or library.sd_bus_message_exit_container(reply) != 1
                        or library.sd_bus_message_exit_container(reply) != 1
                        or library.sd_bus_message_at_end(reply, 1) != 1):
                    raise ValueError("lost reference")
            finally:
                library.sd_bus_message_unref(reply)
            current_boot()
            if time.monotonic() >= deadline:
                raise ValueError("expired reference")

        held()
        print(f"held {run} acquired", flush=True)
        # Ordered fences prevent a buffered old acknowledgement being reused.
        stages = [f"{stage}-{edge}" for stage in ("live", "baseline", "prepare-1", "prepare-2", "prepare-3", "prepare-4", "before-stop", "after-stop", "final")
                  for edge in ("before", "after")]
        with selectors.DefaultSelector() as selector:
            selector.register(sys.stdin, selectors.EVENT_READ)
            index = 0
            for _ in range(19):
                command = b""
                # Pipe reads may split a write. Accumulate one bounded frame,
                # without assuming one read equals one whole command.
                while not command.endswith(b"\n"):
                    if len(command) >= 127 or not selector.select(max(0, deadline - time.monotonic())):
                        raise ValueError("expired or oversized reference command")
                    chunk = os.read(sys.stdin.fileno(), 128 - len(command))
                    if not chunk:
                        raise ValueError("closed reference controller")
                    command += chunk
                if command == b"release\n":
                    call(owner, b"UnrefUnit")
                    library.sd_bus_close(bus)
                    print(f"released {run}", flush=True)
                    return 0
                # Preparation may finish after any one of four observations.
                # Only skip unused whole pairs, never duplicate/reorder a fence.
                if command == b"check before-stop-before\n" and index in (6, 8, 10):
                    index = 12
                if index >= len(stages) or command != f"check {stages[index]}\n".encode("ascii"):
                    raise ValueError("invalid reference command")
                held()
                print(f"held {run} {stages[index]}", flush=True)
                index += 1
    except Exception:
        # Fixed category only; no DBus errors, environment or traceback bytes.
        print("reference=unavailable", file=sys.stderr)
    finally:
        if library is not None and bus.value is not None:
            library.sd_bus_close(bus)
            library.sd_bus_unref(bus)
    return 1


def _run_connector_journal(args: argparse.Namespace) -> int:
    # Own cancellation through initialization and provisional emission. The shell
    # wrapper retains ownership through its final validated file publication.
    # Other capture modes and the negative-reference interface are unchanged.
    cancelled = 0
    signals = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
    handlers = {item: signal.getsignal(item) for item in signals}

    def defer_cancel(signum: int, _frame: Any) -> None:
        nonlocal cancelled
        cancelled = cancelled or signum
        # Propagate to the enclosing nonthrowing capture/delivery owner so
        # its first delivered signal survives both nested handler lifetimes.
        previous = handlers[signum]
        if callable(previous) and previous is not signal.default_int_handler:
            try:
                previous(signum, _frame)
            except BaseException:
                # Inherited handlers cannot unwind handle acquisition/reaping.
                pass

    value = _connector_journal_empty("launch_failure")
    status = 1
    try:
        for item in signals:
            signal.signal(item, defer_cancel)
        try:
            if not cancelled and _initialize_observation_owner():
                if not cancelled:
                    value = capture_connector_journal(args.boot_id, args.since_us, args.until_us)
                    failures = (set(value["losses"]) | set(value["recovery_v2"]["losses"])) - {"observed", "empty", "unavailable"}
                    status = 1 if failures else 0
        except _ConnectorJournalCancelled as exc:
            value, status = exc.value, exc.status
        except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
            value = _connector_journal_empty("query_error")
            status = 1
        if cancelled:
            value["losses"] = sorted((set(value["losses"]) - {"observed", "empty", "unattempted"}) | {"timeout"})
            value["recovery_v2"]["losses"] = sorted(set(value["recovery_v2"]["losses"]) | {"timeout"})
            value["tail_complete"] = value["recovery_v2"]["tail_complete"] = False
        if not _emit_connector_journal(value):
            status = status or 1
        return 128 + cancelled if cancelled else status
    finally:
        for item, handler in handlers.items():
            signal.signal(item, handler)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("receipts", "jobs", "plain", "headscale", "headscale-process", "headscale-read", "headscale-log", "headscale-nodes", "connector", "connector-process", "connector-journal", "negative-unit-reference"), default="receipts")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--invocation-id")
    parser.add_argument("--boot-id")
    parser.add_argument("--since-us", type=int)
    parser.add_argument("--until-us", type=int)
    parser.add_argument("--work")
    parser.add_argument("--headscale-pid")
    parser.add_argument("--connector-pid", type=int)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    if args.mode == "connector-journal":
        return _run_connector_journal(args)
    if args.mode == "negative-unit-reference":
        return hold_negative_unit_reference(args.boot_id, args.run_id)
    if args.mode == "connector-process":
        if args.connector_pid is None or not 1 < args.connector_pid <= 2_147_483_647:
            parser.error("invalid connector PID")
        print(json.dumps(_connector_process(args.connector_pid), separators=(",", ":")))
        return 0
    if args.mode in {"headscale", "connector"}:
        def interrupted(signum: int, frame: Any) -> None:
            for item in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
                signal.signal(item, signal.SIG_IGN)
            raise KeyboardInterrupt
        signal.signal(signal.SIGHUP, interrupted)
        signal.signal(signal.SIGTERM, interrupted)
        signal.signal(signal.SIGINT, interrupted)
        try:
            # Linux shipping capture owns all descendants until bounded reap.
            # Failure to establish that ownership launches no observation.
            if not _initialize_observation_owner():
                print(json.dumps(_connector_unknown("launch_failure") if args.mode == "connector" else {"process_state": "unknown", "log": {"events": [], "losses": ["launch_failure"]}, "nodes": _unknown_nodes("launch_failure")}, separators=(",", ":")))
                return 0
            print(json.dumps(capture_connector() if args.mode == "connector" else capture_headscale(Path(args.work) if args.work else None, args.headscale_pid), separators=(",", ":")))
        except KeyboardInterrupt:
            return 1
        return 0
    if args.mode == "headscale-process":
        try:
            os.kill(int(args.headscale_pid or "0"), 0)
            print("running")
        except ProcessLookupError:
            print("not_running")
        except (OSError, ValueError):
            print("unknown")
        return 0
    if args.mode == "headscale-read":
        try:
            assert args.input is not None
            descriptor = os.open(args.input, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    return 1
                sys.stdout.buffer.write(stream.read(HEADSCALE_BYTES + 1))
            return 0
        except OSError:
            return 1
    if args.mode in {"headscale-log", "headscale-nodes"}:
        raw = sys.stdin.buffer.read(HEADSCALE_BYTES + 1)
        projection = project_headscale_log(raw) if args.mode == "headscale-log" else project_headscale_nodes(raw)
        print(json.dumps(projection, separators=(",", ":")))
        return 0
    if args.mode == "plain":
        filter_plain()
        return 0
    if args.input is None or args.boot_id is None or args.since_us is None or args.until_us is None:
        parser.error("missing observation context")
    try:
        lines = args.input.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        if args.mode == "jobs":
            print('{"jobs":[],"loss":"launch_failure"}')
        else:
            print('{"receipts":[],"losses":["launch_failure"]}')
        return 0
    if args.mode == "jobs":
        result = collect_jobs(lines=lines, boot_id=args.boot_id, since_us=args.since_us, until_us=args.until_us)
    elif args.invocation_id is None:
        result = {"receipts": [], "losses": ["parse_loss"]}
    else:
        result = collect(
            lines=lines,
            invocation_id=args.invocation_id,
            boot_id=args.boot_id,
            since_us=args.since_us,
            until_us=args.until_us,
        )
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
