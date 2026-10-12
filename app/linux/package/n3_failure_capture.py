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


def _closed_json(raw: str) -> Any:
    return json.loads(raw, object_pairs_hook=_unique_members, parse_constant=_invalid_constant)


def _receipt(message: str, *, allow_unknown: bool = False, full: bool = False) -> dict[str, Any] | None:
    if not message.startswith(RECEIPT_PREFIX):
        return None
    try:
        raw = _closed_json(message.removeprefix(RECEIPT_PREFIX))
    except (TypeError, ValueError):
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
        except ValueError:
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
        if not isinstance(timestamp, str) or not timestamp.isdecimal():
            losses.add("parse_loss")
            continue
        if (
            event.get("_SYSTEMD_UNIT") != SIDECAR_UNIT
            or _compact_hex(event.get("_BOOT_ID"), size=32) != expected_boot
            or _compact_hex(event.get("_SYSTEMD_INVOCATION_ID"), size=32) != expected_invocation
            or not since_us <= int(timestamp) <= until_us
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
            event = json.loads(line)
        except ValueError:
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
        if not isinstance(timestamp, str) or not timestamp.isdecimal():
            losses.add("parse_loss")
            continue
        if _compact_hex(event.get("_BOOT_ID"), size=32) != expected_boot or not since_us <= int(timestamp) <= until_us:
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


def _terminate_observation(process: subprocess.Popen[bytes]) -> None:
    """Always terminate the entire owned group, including pipe holders."""
    deadline = time.monotonic() + 1
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=min(0.05, max(0, deadline - time.monotonic())))
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=max(0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        pass
    # The dedicated Linux capture CLI is a subreaper. Adopted pipe holders
    # stay in this command's group; never wait on unrelated fixture children.
    while time.monotonic() < deadline:
        try:
            child, _ = os.waitpid(-process.pid, os.WNOHANG)
        except ChildProcessError:
            break
        if child == 0:
            time.sleep(min(0.01, max(0, deadline - time.monotonic())))


def _bounded_observation(argv: list[str], *, seconds: float, cap: int, data: bytes | None = None) -> tuple[bytes, str]:
    """An independent absolute launch/write/read/wait deadline and byte cap."""
    deadline = time.monotonic() + seconds
    try:
        process = subprocess.Popen(argv, stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    except OSError:
        return b"", "launch_failure"
    output = bytearray()
    total = 0
    written = 0
    try:
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
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return b"", "timeout"
                for key, _ in selector.select(remaining):
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
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return b"", "timeout"
            try:
                status = process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                return b"", "timeout"
            if time.monotonic() >= deadline:
                return b"", "timeout"
            if status != 0:
                return b"", "query_error"
            payload = bytes(output)
            if time.monotonic() >= deadline:
                return b"", "timeout"
            return payload, "observed"
    finally:
        _terminate_observation(process)
        for pipe in (process.stdin, process.stdout, process.stderr):
            if pipe is not None:
                pipe.close()


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


def _connector_unknown(loss: str) -> dict[str, Any]:
    return {"unit": CONNECTOR_UNIT, "binding": None, "listening": None, "bound_port": None,
            "status": "unavailable", "loss": loss,
            "collection": {"observation_budget_seconds": 11, "command_budget_seconds": 13, "kill_grace_seconds": 1, "output_cap_bytes": 4096},
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


def capture_connector() -> dict[str, Any]:
    # Four observations: 1s query, 2s process, 1s query, 2s process,
    # each with at most 1s group reaping, all within the reserved 11s section;
    # launch only when both this stage and its cleanup fit the shared deadline.
    deadline = time.monotonic() + 11
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
        process = _closed_json(raw.decode("ascii"))
        if not isinstance(process, dict):
            raise ValueError
        if set(process) == {"loss"} and process["loss"] in {"truncated", "unavailable_process", "unavailable_child", "attribution_loss", "parse_loss", "query_error"}:
            return _connector_unknown(process["loss"])
        if set(process) != {"loss", "boot_id", "main_pid", "processes", "listener_pid", "listening", "bound_port"}:
            raise ValueError
        if process["loss"] != "observed" or _compact_hex(process["boot_id"], size=32) != process["boot_id"] or type(process["listening"]) is not bool:
            raise ValueError
        rows = process["processes"]
        if not isinstance(rows, list) or not 2 <= len(rows) <= 9:
            raise ValueError
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"pid", "parent_pid", "start_ticks"} or any(type(value) is not int or value <= 0 for value in row.values()):
                raise ValueError
        pids = {row["pid"] for row in rows}
        if len(pids) != len(rows) or pid not in pids or any(row["pid"] != pid and row["parent_pid"] not in pids for row in rows):
            raise ValueError
        if process["listening"] and (type(process["listener_pid"]) is not int or process["listener_pid"] not in pids) or not process["listening"] and process["listener_pid"] is not None:
            raise ValueError
        if type(process["main_pid"]) is not int or process["main_pid"] != pid or process["bound_port"] != (18443 if process["listening"] else None):
            raise ValueError
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
        if _closed_json(repeated.decode("ascii")) != process:
            return _connector_unknown("attribution_loss")
    except (UnicodeError, ValueError, TypeError, RecursionError):
        return _connector_unknown("parse_loss")
    if time.monotonic() >= deadline:
        return _connector_unknown("timeout")
    result = _connector_unknown("observed")
    result.update(binding={key: process[key] for key in ("boot_id", "main_pid", "processes", "listener_pid")},
                  listening=process["listening"], bound_port=process["bound_port"],
                  status=CONNECTOR_STATUS.get(before["StatusText"], "unclassified"))
    result["binding"]["invocation_id"] = _compact_hex(before["InvocationID"], size=32)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("receipts", "jobs", "plain", "headscale", "headscale-process", "headscale-read", "headscale-log", "headscale-nodes", "connector", "connector-process"), default="receipts")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--invocation-id")
    parser.add_argument("--boot-id")
    parser.add_argument("--since-us", type=int)
    parser.add_argument("--until-us", type=int)
    parser.add_argument("--work")
    parser.add_argument("--headscale-pid")
    parser.add_argument("--connector-pid", type=int)
    args = parser.parse_args()
    if args.mode == "connector-process":
        if args.connector_pid is None or not 1 < args.connector_pid <= 2_147_483_647:
            parser.error("invalid connector PID")
        print(json.dumps(_connector_process(args.connector_pid), separators=(",", ":")))
        return 0
    if args.mode in {"headscale", "connector"}:
        def interrupted(signum: int, frame: Any) -> None:
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, interrupted)
        signal.signal(signal.SIGINT, interrupted)
        try:
            # Linux shipping capture owns all descendants until bounded reap.
            # Failure to establish that ownership launches no observation.
            if not sys.platform.startswith("linux") or ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
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
