#!/usr/bin/env python3
"""Definition-owned, stdlib-only disposable guest supervisor and safe postmortem.

This is copied to guest /tmp, never imported from or written into pinned source.
Free text is never an archive field: only fixed categories and typed facts survive.
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import selectors
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import time
from typing import Iterator, Sequence

INNER_SECONDS = 2490
CAPTURE_SECONDS = 30
SHUTDOWN_SECONDS = 30
PYTEST_SECONDS = 2300
MAX_DIAGNOSTIC_BYTES = 65536
INPUT_BYTES = 8192
_LOG_HEAD_BYTES = 4096
_LOG_TAIL_BYTES = 4096
SQL_SECONDS = 2
PACKAGES = ("bash", "curl", "iproute2")
_NODES = (
    ("test_two_orgs_run_tasks_concur", "two_orgs"),
    ("test_mixed_fleet_roundtrip_use", "mixed_fleet"),
    ("test_acceptance_cross_process_", "diy_revoke"),
    ("test_real_diy_acceptance", "diy_acceptance"),
)
_STATUSES = {"pending", "running", "completed", "failed", "blocked", "cancelled",
             "in_progress", "escalated", "superseded"}
_CATEGORIES = {
    "does not have the canonical": "workspace_instruction_pair_refused",
    "is not initialized (missing": "workspace_not_initialized",
    "WorkspaceNotInitialized": "workspace_not_initialized",
    "authority selector is uninitialized": "authority_selector_uninitialized",
    "SymlinkMaterializationError": "skill_materialization_failed",
    "executor not found": "executor_missing",
    "session_mismatch": "callback_session_mismatch",
    "agent invocation failed:": "agent_invocation_failed",
}


def _encoded(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _write(path: Path, value: object) -> None:
    data = _encoded(value)
    if len(data) > MAX_DIAGNOSTIC_BYTES:
        raise ValueError("diagnostic_output_cap")
    with path.open("xb") as stream:
        stream.write(data)


def _stop_owned(process: subprocess.Popen[bytes]) -> None:
    # Only the process group created by this invocation; no process census.
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            break
        if process.poll() is None:
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                continue
        if sig == signal.SIGTERM:
            # A finished leader can still have descendants holding inherited pipes.
            continue
    process.wait(timeout=0.5)


def run_workload(
    argv: Sequence[str], *, deadline: float, seconds: float, reserve: float,
    output: bool = False,
) -> tuple[int, bytes, str]:
    """All phases spend the same deadline, including bounded group teardown."""
    budget = min(seconds, deadline - time.monotonic() - reserve)
    if budget <= 1.5:
        return 124, b"", "budget_exhausted"
    command_deadline = time.monotonic() + budget - 1.5
    try:
        process = subprocess.Popen(
            argv, start_new_session=True,
            stdout=subprocess.PIPE if output else None,
            stderr=subprocess.DEVNULL if output else None,
        )
    except OSError:
        return 127, b"", "executable_unavailable"
    data = bytearray()
    try:
        if output:
            assert process.stdout is not None
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = command_deadline - time.monotonic()
                    if remaining <= 0:
                        return 124, bytes(data), "timeout"
                    for key, _ in selector.select(min(remaining, 0.1)):
                        chunk = os.read(key.fd, INPUT_BYTES + 1)
                        if not chunk:
                            selector.unregister(key.fileobj)
                        else:
                            data.extend(chunk)
                            if len(data) > INPUT_BYTES:
                                return 83, b"", "output_cap"
        try:
            code = process.wait(timeout=max(0.001, command_deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            return 124, bytes(data), "timeout"
        return (128 - code if code < 0 else code), bytes(data), "ok" if code == 0 else "nonzero"
    finally:
        _stop_owned(process)
        if process.stdout is not None:
            process.stdout.close()


def _ipv4(deadline: float) -> dict[str, object]:
    code, raw, outcome = run_workload(
        ["ip", "-4", "-o", "addr", "show"], deadline=deadline,
        seconds=10, reserve=CAPTURE_SECONDS + SHUTDOWN_SECONDS, output=True,
    )
    receipt: dict[str, object] = {"command_status": code, "discovery": outcome,
                                  "validator": "not_run", "bind": "not_run"}
    if code != 0:
        return receipt
    # This import uses the unchanged pinned shipping validator, after frozen sync.
    from runtime.remote_access.network import NetworkAddressError, validate_customer_network_address
    try:
        text = raw.decode("ascii")
    except UnicodeError:
        receipt["discovery"] = "malformed"
        return receipt
    receipt["discovery"] = "no_address"
    for line in text.splitlines():
        fields = line.split()
        if "inet" not in fields:
            receipt["discovery"] = "malformed"
            continue
        index = fields.index("inet")
        try:
            interface = ipaddress.IPv4Interface(fields[index + 1])
        except (IndexError, ValueError):
            receipt["discovery"] = "malformed"
            continue
        address = str(interface.ip)
        if interface.ip.is_loopback:
            receipt["discovery"] = "loopback_only"
            continue
        try:
            validate_customer_network_address(address)
        except NetworkAddressError:
            receipt.update(discovery="validator_rejected", validator="rejected")
            continue
        receipt.update(discovery="address", validator="accepted", address=address)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind((address, 0))
                receipt["bind"] = "bound"
        except OSError:
            receipt["bind"] = "failed"
            continue
        return receipt
    return receipt


def _category(value: object) -> str:
    if value is None or value == "":
        return "none"
    if not isinstance(value, str) or len(value.encode("utf-8")) > INPUT_BYTES:
        return "omitted"
    for marker, category in _CATEGORIES.items():
        if marker in value:
            return category
    return "omitted"


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


@contextmanager
def _owned_directory(path: Path) -> Iterator[int]:
    """Walk literal absolute ancestry without resolving symlinks."""
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("unsafe_path")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    chain = []
    try:
        for part in path.parts[1:]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
            chain.append((os.fstat(fd).st_dev, os.fstat(fd).st_ino))
        info = os.fstat(fd)
        if info.st_uid != os.getuid():
            raise ValueError("foreign_owner")
        yield fd
        check_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part, identity in zip(path.parts[1:], chain):
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=check_fd)
                os.close(check_fd)
                check_fd = next_fd
                if (os.fstat(check_fd).st_dev, os.fstat(check_fd).st_ino) != identity:
                    raise ValueError("ancestor_changed")
        finally:
            os.close(check_fd)
        if _identity(os.stat(path, follow_symlinks=False)) != _identity(os.fstat(fd)):
            raise ValueError("directory_changed")
    finally:
        os.close(fd)


@contextmanager
def _owned_file(directory: Path, name: str) -> Iterator[tuple[int, int]]:
    with _owned_directory(directory) as parent:
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_nlink != 1 or _identity(info) != _identity(before)):
                raise ValueError("unsafe_file")
            yield parent, fd
            if (_identity(os.fstat(fd)) != _identity(info)
                    or _identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) != _identity(info)):
                raise ValueError("file_changed")
        finally:
            os.close(fd)


def _read_tasks(directory: Path, deadline: float, remaining: int) -> dict[str, object]:
    result: dict[str, object] = {}
    with _owned_file(directory, "happyranch.db") as (parent, db_fd):
        # Refuse unsafe/changing sidecars and never read a main file past a live WAL.
        sidecars: dict[str, tuple[int, int, int, int, int] | None] = {}
        for name in ("happyranch.db-wal", "happyranch.db-shm"):
            try:
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                sidecars[name] = None
            else:
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
                    raise ValueError("unsafe_sidecar")
                sidecars[name] = _identity(info)
        # mode=ro alone can create WAL/SHM in a writable directory. Never do that,
        # and never ignore an existing WAL. A closed, checkpointed WAL-mode main
        # file with BOTH sidecars absent may be read lock-free, then revalidated.
        wal_mode = os.pread(db_fd, 20, 0)[18:20] == b"\x02\x02"
        if wal_mode and any(value is not None for value in sidecars.values()):
            raise ValueError("wal_evidence_unavailable")
        uri = f"file:/proc/self/fd/{parent}/happyranch.db?mode=ro"
        if wal_mode:
            uri += "&immutable=1"
        sql_deadline = min(deadline, time.monotonic() + SQL_SECONDS)
        with closing(sqlite3.connect(uri, uri=True,
                                     timeout=max(0.001, sql_deadline - time.monotonic()))) as connection:
            connection.execute("PRAGMA query_only=ON")
            connection.set_progress_handler(lambda: int(time.monotonic() >= sql_deadline), 100)
            def query(sql: str, parameters: tuple[object, ...] = ()) -> sqlite3.Cursor:
                remaining_ms = int((sql_deadline - time.monotonic()) * 1000)
                if remaining_ms <= 0:
                    raise sqlite3.OperationalError("diagnostic_read_deadline")
                connection.execute(f"PRAGMA busy_timeout={remaining_ms}")
                return connection.execute(sql, parameters)
            schemas = {
                "tasks": {"id": "TEXT", "status": "TEXT", "note": "TEXT"},
                "task_results": {"task_id": "TEXT"},
                "audit_log": {"task_id": "TEXT", "action": "TEXT"},
            }
            # Only these three literal PRAGMAs; no external SQL or production initializer.
            columns = [query(sql).fetchall() for sql in (
                "PRAGMA table_info(tasks)", "PRAGMA table_info(task_results)", "PRAGMA table_info(audit_log)",
            )]
            for (table, required), rows in zip(schemas.items(), columns):
                found = {row[1]: row[2].upper() for row in rows}
                if any(found.get(column) != kind for column, kind in required.items()):
                    raise ValueError("unknown_schema")
                kind = query(
                    "SELECT type FROM sqlite_master WHERE name=?", (table,)
                ).fetchone()
                if kind != ("table",):
                    raise ValueError("unknown_schema")
            records = query(
                "SELECT substr(id,1,40),substr(status,1,32),substr(CAST(note AS BLOB),1,8193) FROM tasks ORDER BY id LIMIT 9"
            ).fetchall()
            result["tasks_truncated"] = len(records) > remaining
            tasks = []
            for task_id, status, note in records[:remaining]:
                if (not isinstance(task_id, str) or re.fullmatch(r"TASK-[0-9]{3,12}", task_id) is None
                        or status not in _STATUSES):
                    raise ValueError("unknown_record")
                results = query(
                    "SELECT count(*) FROM task_results WHERE task_id=?", (task_id,)
                ).fetchone()[0]
                sessions = query(
                    "SELECT count(*) FROM audit_log WHERE task_id=? AND action='session_start'", (task_id,)
                ).fetchone()[0]
                safe_note = note.decode("utf-8", errors="replace") if isinstance(note, bytes) else note
                tasks.append({"task_id": task_id, "status": status, "note_category": _category(safe_note),
                              "result_count": results, "session_start_count": sessions,
                              "executor_evidence": "unavailable"})
            result["tasks"] = tasks
        for name, before in sidecars.items():
            try:
                after = _identity(os.stat(name, dir_fd=parent, follow_symlinks=False))
            except FileNotFoundError:
                after = None
            if after != before:
                raise ValueError("sidecar_changed")
    return result


def _read_owned_log(directory: Path, deadline: float) -> dict[str, object]:
    """Return only validated complete-line categories and byte omission facts."""
    with _owned_file(directory, "daemon.log") as (_, fd):
        size = os.fstat(fd).st_size

        def read_interval(offset: int, length: int) -> bytes:
            data = bytearray()
            while len(data) < length:
                if time.monotonic() >= deadline:
                    raise ValueError("diagnostic_read_deadline")
                chunk = os.pread(fd, length - len(data), offset + len(data))
                if not chunk:
                    raise ValueError("log_changed_or_short")
                data.extend(chunk)
            return bytes(data)

        discarded = 0
        if size <= INPUT_BYTES:
            windows = [read_interval(0, size)]
            read = size
        else:
            head = read_interval(0, _LOG_HEAD_BYTES)
            tail = read_interval(size - _LOG_TAIL_BYTES, _LOG_TAIL_BYTES)
            # Without an extra boundary probe, neither edge fragment is evidence.
            head_end = head.rfind(b"\n") + 1
            tail_start = tail.find(b"\n") + 1
            if tail_start == 0:
                tail_start = len(tail)
            discarded = len(head) - head_end + tail_start
            windows = [head[:head_end], tail[tail_start:]]
            read = len(head) + len(tail)
        events = []
        recognized = 0
        for window in windows:
            for line in window.split(b"\n"):
                category = _category(line.decode("utf-8", errors="replace"))
                if category not in {"none", "omitted"}:
                    recognized += 1
                    if len(events) < 32:
                        events.append(category)
        omitted = size - read + discarded
        facts = {"event_categories": events, "log_size_bytes": size,
                 "log_read_bytes": read, "log_gap_bytes": size - read,
                 "log_boundary_discarded_bytes": discarded,
                 "log_scanned_bytes": read - discarded, "log_omitted_bytes": omitted,
                 "log_truncated": omitted > 0, "log_text_omitted": True,
                 "log_event_limit_reached": recognized > 32}
    # _owned_file and its ancestry validate before any provisional fact escapes.
    return facts


def _read_two_org_prelaunch_exception(directory: Path, org: str, *, deadline: float,
                                     revision: str | None, source_digest: str | None) -> dict:
    """Consume only a complete private sidecar at the authenticated source."""
    unavailable = {"org": org if org in {"alpha", "beta"} else "unknown", "prelaunch_capture": "unavailable"}
    if (org not in {"alpha", "beta"} or not isinstance(revision, str)
            or not isinstance(source_digest, str) or time.monotonic() >= deadline):
        return unavailable
    try:
        with _owned_directory(directory) as parent:
            if stat.S_IMODE(os.fstat(parent).st_mode) != 0o700:
                return unavailable
        with _owned_file(directory, org + "-TASK-001.json") as (_, fd):
            info = os.fstat(fd)
            if stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 1024:
                return unavailable
            data = os.read(fd, 1025)
            if len(data) != info.st_size or not data.endswith(b"\n") or time.monotonic() >= deadline:
                return unavailable
            def closed_pairs(items):
                result = {}
                for key, value in items:
                    if key in result:
                        raise ValueError("duplicate_capture_key")
                    result[key] = value
                return result
            row = json.loads(data.decode("ascii"), object_pairs_hook=closed_pairs)
            from tests.helpers.two_org_prelaunch_capture.sitecustomize import validate_record
            validate_record(row, org=org, revision=revision, source_digest=source_digest)
        if time.monotonic() >= deadline:
            return unavailable
        return row
    except (OSError, ValueError, TypeError, KeyError):
        return unavailable


def capture_diagnostics(basetemp: Path, *, deadline: float,
                        source: Path | None = None, source_sha: str | None = None) -> dict[str, object]:
    receipt: dict[str, object] = {"nodes": [], "nodes_truncated": False,
                                 "cause": "unknown", "raw_text": "omitted"}
    nodes: list[dict[str, object]] = []
    try:
        with _owned_directory(basetemp) as base:
            names = sorted(os.listdir(base))
            selected = [(name, kind) for name in names for prefix, kind in _NODES
                        if re.fullmatch(re.escape(prefix) + r"[0-9]{1,4}", name)]
            selected.sort(key=lambda entry: (
                ("two_orgs", "mixed_fleet", "diy_revoke", "diy_acceptance").index(entry[1]), entry[0]))
            receipt["nodes_truncated"] = len(selected) > 4
            for name, kind in selected[:4]:
                if time.monotonic() >= deadline:
                    receipt["deadline_exhausted"] = True
                    break
                node: dict[str, object] = {"node": name, "kind": kind, "orgs": [],
                                           "executor_evidence": "unavailable"}
                try:
                    with _owned_directory(basetemp / name):
                        orgs = []
                        remaining = 8
                        for org in ("alpha", "beta", "test"):
                            facts: dict[str, object] = {"org": org}
                            try:
                                facts.update(_read_tasks(basetemp / name / "runtime/orgs" / org, deadline, remaining))
                                remaining -= len(facts.get("tasks", []))
                            except FileNotFoundError:
                                facts["unavailable"] = "missing"
                            except (OSError, ValueError, sqlite3.Error) as exc:
                                facts["unavailable"] = (str(exc) if isinstance(exc, ValueError)
                                                        else "sqlite_unavailable" if isinstance(exc, sqlite3.Error)
                                                        else "unsafe_or_unreadable")
                            orgs.append(facts)
                        node["orgs"] = orgs
                        if kind == "two_orgs":
                            digest = None
                            if source is not None and source_sha is not None:
                                try:
                                    from tests.helpers.two_org_prelaunch_capture.sitecustomize import source_binding
                                    digest = source_binding(source, source_sha, deadline=deadline)["digest"]
                                except (OSError, ValueError):
                                    pass
                            directory = basetemp / name / ".happyranch/two-org-prelaunch"
                            node["prelaunch_exceptions"] = [
                                _read_two_org_prelaunch_exception(directory, org, deadline=deadline,
                                                                 revision=source_sha, source_digest=digest)
                                for org in ("alpha", "beta")]
                        try:
                            node.update(_read_owned_log(basetemp / name / ".happyranch", deadline))
                        except (OSError, ValueError):
                            node["log_unavailable"] = True
                            node["event_categories"] = []
                except (OSError, ValueError):
                    node = {"node": name, "unavailable": "unsafe_or_changed"}
                nodes.append(node)
    except FileNotFoundError:
        receipt["unavailable"] = "basetemp_missing"
    except (OSError, ValueError):
        receipt["unavailable"] = "basetemp_unsafe_or_changed"
        nodes = []
    receipt["nodes"] = nodes
    return receipt


def _source_digest(source: Path, deadline: float) -> str:
    digest = hashlib.sha256()
    for root, dirs, files in os.walk(source, followlinks=False):
        dirs[:] = sorted(name for name in dirs if name not in {".git", "__pycache__"})
        for name in dirs[:]:
            path = Path(root) / name
            if path.is_symlink():
                digest.update(str(path.relative_to(source)).encode() + b"\0" + os.readlink(path).encode())
                dirs.remove(name)
        for name in sorted(files):
            if time.monotonic() >= deadline:
                raise TimeoutError("source_digest_deadline")
            path = Path(root) / name
            digest.update(str(path.relative_to(source)).encode() + b"\0")
            if path.is_symlink():
                digest.update(os.readlink(path).encode())
            else:
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(65536), b""):
                        if time.monotonic() >= deadline:
                            raise TimeoutError("source_digest_deadline")
                        digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, required=True)
    commands = parser.add_subparsers(dest="phase", required=True)
    start = commands.add_parser("start")
    for name in ("source", "artifacts", "basetemp"):
        start.add_argument(f"--{name}", type=Path, required=True)
    start.add_argument("--source-sha", default=None)
    command = commands.add_parser("command")
    command.add_argument("--seconds", type=float, required=True)
    command.add_argument("--reserve", type=float, required=True)
    command.add_argument("argv", nargs=argparse.REMAINDER)
    commands.add_parser("capture")
    commands.add_parser("ipv4")
    commands.add_parser("packages")
    finish = commands.add_parser("finish")
    for name in ("workload_status", "summary_status", "capture_status"):
        finish.add_argument(name, type=int)
    args = parser.parse_args()
    if args.phase == "start":
        deadline = time.monotonic() + INNER_SECONDS
        _write(args.state, {"deadline": deadline, "source": str(args.source),
                            "artifacts": str(args.artifacts), "basetemp": str(args.basetemp),
                            "source_sha": args.source_sha,
                            "source_digest": _source_digest(args.source, deadline - 60)})
        return 0
    state = json.loads(args.state.read_text())
    artifacts = Path(state["artifacts"])
    if args.phase == "command":
        argv = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
        if argv[:3] == ["uv", "run", "pytest"]:
            argv = ["uv", "run", "python", str(Path(state["source"]) / "tests/helpers/integration_parent.py"),
                    "--", "pytest", *argv[3:]]
        status, _, _ = run_workload(argv, deadline=state["deadline"], seconds=args.seconds,
                                    reserve=args.reserve)
        return status
    if args.phase == "ipv4":
        sys.path.insert(0, state["source"])
        _write(artifacts / "guest-ipv4.json", _ipv4(state["deadline"]))
    elif args.phase == "packages":
        snapshots = []
        for suffix in ("before", "after"):
            path = args.state.parent / f"happyranch-packages-{suffix}.txt"
            if path.stat().st_size > MAX_DIAGNOSTIC_BYTES:
                raise ValueError("package_identity_cap")
            snapshot = {}
            for line in path.read_text(encoding="ascii").splitlines():
                package, version = line.split()
                if (re.fullmatch(r"[a-z0-9][a-z0-9+.:_-]*", package) is None
                        or re.fullmatch(r"[a-zA-Z0-9+.:~_-]+", version) is None):
                    raise ValueError("package_identity_malformed")
                snapshot[package] = version
            snapshots.append(snapshot)
        before, after = snapshots
        _write(artifacts / "guest-packages.json", {
            "direct": {name: after[name] for name in PACKAGES},
            "transitive_added_or_changed": {name: version for name, version in after.items()
                                            if name not in PACKAGES and before.get(name) != version},
        })
    elif args.phase == "capture":
        sys.path.insert(0, state["source"])
        _write(artifacts / "guest-diagnostics.json", capture_diagnostics(
            Path(state["basetemp"]), deadline=min(state["deadline"] - SHUTDOWN_SECONDS,
                                                 time.monotonic() + CAPTURE_SECONDS),
            source=Path(state["source"]), source_sha=state.get("source_sha")))
    elif args.phase == "finish":
        after = _source_digest(Path(state["source"]), state["deadline"])
        unchanged = after == state["source_digest"]
        receipt = {"workload_status": args.workload_status, "summary_status": args.summary_status,
                   "capture_status": args.capture_status, "diagnostics_before_exit": args.capture_status == 0,
                   "source_unchanged": unchanged, "source_digest_before": state["source_digest"],
                   "source_digest_after": after, "helper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
        evidence_paths = [artifacts / name for name in (
            "guest-diagnostics.json", "guest-packages.json", "guest-ipv4.json")]
        receipt["setup_evidence_complete"] = all(path.is_file() for path in evidence_paths)
        receipt["diagnostic_bytes"] = sum(path.stat().st_size for path in evidence_paths if path.is_file()) + len(_encoded(receipt)) + 64
        if receipt["diagnostic_bytes"] > MAX_DIAGNOSTIC_BYTES:
            raise ValueError("combined_diagnostic_cap")
        _write(artifacts / "guest-result.json", receipt)
        return 0 if unchanged else 91
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
