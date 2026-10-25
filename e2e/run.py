#!/usr/bin/env python3
"""Standalone, bounded disposable-runner entry; never a pytest selection."""
from __future__ import annotations

import argparse
import errno
import hashlib
import json
import math
import os
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path
from contextlib import contextmanager
from typing import Any, Callable

from owned import Owned, identity


# Existing baseline artifact presence contract; no content/projection oracle.
BASELINE_ARTIFACTS = ["observations.json", "browser-events.json", "daemon.txt",
                      "lifecycle.png", "response-loss.png", "org-switch-0.png", "org-switch-1.png",
                      "org-switch-2.png", "org-switch-return-revalidated.png", "browser-final.png", "e07.json", "e07-prefix.json", "launch-witnesses.jsonl",
                      "e07-parent.png", "e07-child.png", "e07-list.png"]


def _diagnostic_write(line: bytes, end: float) -> bool:
    """One caller-charged native-fd2 opportunity, with no latency guarantee."""
    if type(line) is not bytes or not 0 < len(line) <= 512 or time.monotonic() >= end:
        return False
    try:
        count = os.write(2, line)  # Unchanged inherited fd; no buffering/flag work.
    except TimeoutError:
        raise  # A real raising alarm is never an ordinary optional I/O failure.
    except OSError:
        return False
    return count == len(line) and time.monotonic() < end


def _diagnostic_packet(data: bytes) -> bool:
    if len(data) != 23:
        return False
    fields = data.split(b":")
    return (len(fields) == 8 and fields[:2] == [b"HRD2", b"S"]
            and fields[2] in (b"PH", b"BD", b"RS", b"CT", b"AC", b"TD", b"EV", b"BR", b"OT")
            and fields[3] in (b"RM", b"PH", b"BD", b"TS", b"WE", b"RD", b"CS", b"RS", b"CL", b"EV", b"MN", b"AL", b"UN")
            and len(fields[4]) == len(fields[5]) == 2
            and all(c in b"0123456789ABCDEF" for c in fields[4] + fields[5])
            and fields[6] in (b"P", b"A")
            and ((fields[7] in (b"P\n", b"E\n")
                  and 1 <= int(fields[4], 16) <= 31 and 1 <= int(fields[5], 16) <= 127)
                 or (fields[7] == b"U\n" and fields[4] == fields[5] == b"00")))


class _ScenarioDiagnostic:
    """Invocation-local ownership; refusal/charged calls cannot be repaired."""
    def __init__(self, end: float) -> None:
        self.end = end
        self.receiver = self.sender = None
        self.receiver_close_started = self.sender_close_started = False
        self.receiver_released = self.sender_released = False
        self.ready = False
        self.sample_claimed = self.probe_claimed = self.output_claimed = False
        self.descriptor = None
        try:
            if time.monotonic() >= end:
                return
            if not all(hasattr(socket, name) for name in ("SOCK_NONBLOCK", "SOCK_CLOEXEC", "MSG_DONTWAIT", "MSG_PEEK")):
                return
            self.receiver, self.sender = socket.socketpair(
                socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_NONBLOCK | socket.SOCK_CLOEXEC)
            self.receiver.shutdown(socket.SHUT_WR)
            self.sender.shutdown(socket.SHUT_RD)
            self.descriptor = self.sender.fileno()
            self.ready = (self.descriptor > 2 and not self.receiver.getblocking() and not self.sender.getblocking()
                          and not self.receiver.get_inheritable() and not self.sender.get_inheritable()
                          and time.monotonic() < end)
        except TimeoutError:
            raise
        except OSError:
            self.ready = False  # Ordinary setup decline precedes ONE launch choice.
        finally:
            if not self.ready:
                self.release()

    def release_endpoint(self, name: str) -> bool:
        if getattr(self, name + "_close_started"):
            return getattr(self, name + "_released")
        setattr(self, name + "_close_started", True)
        endpoint = getattr(self, name)
        if endpoint is None:
            return False
        try:
            endpoint.close()
        except TimeoutError:
            raise
        except OSError:
            self.ready = False
            return False
        setattr(self, name + "_released", True)
        return True

    def release(self) -> None:
        try:
            self.release_endpoint("sender")
        finally:
            self.release_endpoint("receiver")

    def receive(self) -> None:
        # Called ONLY after the original loop's direct exit and original phase
        # finalization/log closure. Endpoint close alone is not termination.
        if not self.ready or not self.sender_released or self.sample_claimed:
            return
        self.sample_claimed = True
        if time.monotonic() >= self.end:
            return
        try:
            data, ancillary, flags, _ = self.receiver.recvmsg(513, 0, socket.MSG_DONTWAIT)
        except TimeoutError:
            raise
        except OSError:
            return  # Including first EAGAIN: no valid sample, so no probe.
        if ancillary or flags or not _diagnostic_packet(data):
            return
        if not self.ready or not self.sender_released or time.monotonic() >= self.end:
            return
        self.probe_claimed = True
        try:
            self.receiver.recvmsg(1, 0, socket.MSG_PEEK | socket.MSG_DONTWAIT)
        except TimeoutError:
            raise
        except OSError as exc:
            if exc.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                return
        else:
            return  # ANY tuple, including b''/invalid/truncated/ancillary, refuses ALL.
        if not self.ready or not self.sender_released or time.monotonic() >= self.end:
            return
        if not self.release_endpoint("receiver"):
            return
        self.output_claimed = True
        if self.ready and self.receiver_released and time.monotonic() < self.end:
            _diagnostic_write(b"HRD1:controller:" + data, self.end)


def arm_deadline(deadline: float) -> None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("exhausted absolute deadline")
    # alarm(int(remaining)) can disable the timer for a positive fraction.
    signal.setitimer(signal.ITIMER_REAL, remaining)


def copy_npm_downloads(source: Path, destination: Path, deadline: float) -> None:
    """Copy only ordinary _cacache trees; no shared live state or race fence."""
    def fail(error: OSError) -> None:
        raise error

    def validate(path: Path) -> bool:
        if not path.is_absolute() or path.name != "_cacache" or ".." in path.parts:
            raise ValueError("invalid npm download-store path")
        # Check parents too: even a missing leaf must not conceal an alias.
        for parent in (*reversed(path.parents), path):
            arm_deadline(deadline)
            try:
                info = parent.lstat()
            except FileNotFoundError:
                return False
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError(f"non-directory or aliased npm download path: {parent}")
        for current, directories, files in os.walk(path, onerror=fail, followlinks=False):
            arm_deadline(deadline)
            for name in directories + files:
                arm_deadline(deadline)
                entry = Path(current) / name
                info = entry.lstat()
                if not (stat.S_ISDIR(info.st_mode)
                        or (stat.S_ISREG(info.st_mode) and info.st_nlink == 1)):
                    raise ValueError(f"aliased or special npm download entry: {entry}")
        return True

    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("overlapping npm download stores")
    present = validate(source)
    validate(destination)
    if present:
        arm_deadline(deadline)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination, dirs_exist_ok=True, copy_function=shutil.copyfile)
    arm_deadline(deadline)


def validate_timing(row: dict) -> None:
    """Require complete, bounded launcher receipts in both daily consumers."""
    timing = row["timing"]
    started, action_end, hard_end = (timing[key] for key in ("started", "action_deadline", "hard_deadline"))
    if not all(math.isfinite(value) for value in (started, action_end, hard_end)):
        raise ValueError("nonfinite timing")
    if action_end != started + 720 or hard_end != started + 900:
        raise ValueError("changed outer deadlines")
    action = timing["action"]
    if (not started <= action["finished"] <= action_end or action["deadline"] != action_end
            or action["allowance_seconds"] != 720 or action["elapsed_seconds"] != action["finished"] - started):
        raise ValueError("action budget exhausted")
    cleanup, export = timing["cleanup"], timing["export"]
    for phase, cap, outer in ((cleanup, 60, min(action_end + 60, hard_end)), (export, 120, hard_end)):
        if (phase["ok"] is not True or not action["finished"] <= phase["entered"]
                or phase["deadline"] != min(phase["entered"] + cap, outer)
                or not phase["entered"] <= phase["finished"] < phase["deadline"]
                or phase["allowance_seconds"] != phase["deadline"] - phase["entered"]
                or phase["elapsed_seconds"] != phase["finished"] - phase["entered"]):
            raise ValueError("missing, failed or inconsistent reserve timing")
    if cleanup["finished"] > export["entered"]:
        raise ValueError("overlapping cleanup/export")
    if (row["total_seconds"] != export["finished"] - started
            or row["evidence_seconds"] != export["elapsed_seconds"]):
        raise ValueError("inconsistent elapsed timing")
    for phase in row["phases"]:
        cap = 480 if phase["name"] == "scenarios" and row["exercise"] == "baseline" else 240
        if (phase["cap_seconds"] != cap
                or phase["deadline"] != min(phase["entered"] + cap, action_end)
                or phase["allowance_seconds"] != phase["deadline"] - phase["entered"]
                or not started <= phase["entered"] <= phase["finished"] <= phase["deadline"]):
            raise ValueError("inconsistent command timing")


ADMISSION_FIELDS = ("event", "cron", "repository", "workflow_ref", "workflow_sha",
                    "run_id", "run_attempt", "source_ref", "source_sha", "source_tree")


def validate_admission(row: dict, admission: dict) -> None:
    """Join a receipt to the independently admitted Actions event/run/source."""
    if any(key not in row or row[key] != admission[key] for key in ADMISSION_FIELDS):
        raise ValueError("Actions event/run/source receipt mismatch")


def daily_admission(source: Path) -> dict:
    """Admit the daily schedule or an explicitly authorized main dispatch.

    Environment/payload checks are admission validation, not a hostile-runner
    security boundary or execution allocation. No CLI event override exists.
    """
    event_name = os.environ.get("GITHUB_EVENT_NAME")
    if event_name not in {"schedule", "workflow_dispatch"}:
        raise RuntimeError("fresh Product E2E requires schedule or authorized main workflow_dispatch")
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        raise RuntimeError("Actions event payload is required")
    event = json.loads(Path(event_path).read_text())
    repository = event["repository"]
    branch = repository["default_branch"]
    repo = repository["full_name"]
    if not isinstance(branch, str) or not branch or not isinstance(repo, str) or not repo:
        raise RuntimeError("missing default-branch/repository identity")
    ref = "refs/heads/" + branch
    if event_name == "schedule":
        if event.get("schedule") != "0 19 * * *":
            raise RuntimeError("daily cron mismatch")
    elif branch != "main" or event.get("ref") != "refs/heads/main" or "schedule" in event:
        raise RuntimeError("manual dispatch requires actual main/default branch without a schedule")
    sha = os.environ.get("GITHUB_SHA", "")
    workflow_ref = repo + "/.github/workflows/e2e.yml@" + ref
    workflow_sha = os.environ.get("GITHUB_WORKFLOW_SHA")
    run_id, attempt = (os.environ.get(key, "") for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"))
    if (os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("GITHUB_REPOSITORY") != repo
            or os.environ.get("GITHUB_REF") != ref
            or os.environ.get("GITHUB_WORKFLOW_REF") != workflow_ref
            or workflow_sha != sha
            or len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha)
            or any(not value or value[0] not in "123456789" or any(c not in "0123456789" for c in value)
                   for value in (run_id, attempt))):
        raise RuntimeError("Actions default-branch/repository/workflow/run/source metadata mismatch")
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=source, text=True).strip()
    if actual != sha or len(tree) != 40 or any(c not in "0123456789abcdef" for c in tree):
        raise RuntimeError("checkout differs from immutable Actions source or has invalid tree")
    return dict(event=event_name, cron=event.get("schedule"), repository=repo,
                workflow_ref=workflow_ref, workflow_sha=workflow_sha, run_id=run_id,
                run_attempt=attempt, source_sha=sha, source_ref=ref, source_tree=tree)


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
def _failure_directories(root: Path, check: Callable[[], None], *, coord: bool = True,
                         owned: bool = True) -> Any:
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
            links.append((current, name, descriptor, info, owned and index >= len(parts) - 1))
            current = descriptor
            verify()
        yield current, verify
        if coord:
            verify()
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _failure_file(info: os.stat_result) -> None:
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
        raise ValueError("unsafe diagnostic file")


def _read_failure_categories(root: Path | None, source: object, check: Callable[[], None],
                             missing_ok: bool) -> tuple[dict, str | None, str]:
    unavailable = dict(availability="unavailable", snapshot=None)
    if not _failure_source(source):
        return unavailable, None, "source_unavailable"  # Internal only; never projected.
    if root is None:
        return unavailable, None, "root_unset"

    def pairs(items: list[tuple]) -> dict:
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate diagnostic key")
            result[key] = value
        return result

    def invalid_constant(_value: str) -> None:
        raise ValueError("nonfinite diagnostic constant")

    def slot(value: object) -> dict | None:
        if value is None:
            return None
        if type(value) is not dict or set(value) != {"stage", "error", "at"}:
            raise ValueError("invalid diagnostic slot")
        stage, error, at = (value[key] for key in ("stage", "error", "at"))
        if (type(stage) is not str or stage not in {"phase", "bounded", "restore", "context", "action",
                                                    "teardown", "evidence", "browser", "other"}
                or type(error) is not str or error not in {"unicode", "timeout", "interruption", "assertion",
                                                          "value", "io", "runtime", "other"}
                or type(at) not in (int, float) or not 0 <= at <= 9007199254740991
                or not math.isfinite(at)):
            raise ValueError("invalid diagnostic value")
        return dict(stage=stage, error=error, at=at)

    try:
        check()
        try:
            with _failure_directories(root, check) as (directory, verify):
                final, pending = "failure-categories.json", ".failure-categories.pending"

                def no_pending() -> None:
                    check()
                    try:
                        os.stat(pending, dir_fd=directory, follow_symlinks=False)
                    except FileNotFoundError:
                        check()
                        return
                    raise ValueError("unfinished diagnostic publication")

                no_pending()
                try:
                    named = os.stat(final, dir_fd=directory, follow_symlinks=False)
                except FileNotFoundError:
                    verify()
                    no_pending()
                    check()
                    return unavailable, None, "final_missing"  # Literal final ENOENT only.
                _failure_file(named)
                check()
                descriptor = os.open(final, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                try:
                    check()
                    before = os.fstat(descriptor)
                    _failure_file(before)
                    if (before.st_dev, before.st_ino) != (named.st_dev, named.st_ino) or before.st_size > 4096:
                        raise ValueError("diagnostic identity or size")
                    verify()
                    check()
                    data = os.read(descriptor, 4097)  # ONE bounded read, no EOF retry.
                    check()
                    after = os.fstat(descriptor)
                    named = os.stat(final, dir_fd=directory, follow_symlinks=False)
                    _failure_file(after)
                    _failure_file(named)
                    stamp = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                    if (stamp(before) != stamp(after) or stamp(after) != stamp(named)
                            or len(data) > 4096 or len(data) != before.st_size):
                        raise ValueError("short or changed diagnostic snapshot")
                    no_pending()
                    verify()
                    check()
                    decoded = data.decode("utf-8", errors="strict")
                    check()
                    value = json.loads(decoded, object_pairs_hook=pairs, parse_constant=invalid_constant)
                    check()
                    if (type(value) is not dict or set(value) != {"schema", "source_sha", "first_observed", "context_capture"}
                            or type(value["schema"]) is not int or value["schema"] != 1
                            or not _failure_source(value["source_sha"]) or value["source_sha"] != source):
                        raise ValueError("invalid diagnostic envelope")
                    snapshot = dict(schema=1, source_sha=source, first_observed=slot(value["first_observed"]),
                                    context_capture=slot(value["context_capture"]))
                    # Admit only while file/parent/pending identities still agree.
                    verify()
                    no_pending()
                    named = os.stat(final, dir_fd=directory, follow_symlinks=False)
                    _failure_file(named)
                    if stamp(named) != stamp(before):
                        raise ValueError("diagnostic identity changed while parsing")
                    check()
                finally:
                    os.close(descriptor)
            check()
            return dict(availability="available", snapshot=snapshot), None, "available"
        except FileNotFoundError as exc:
            if missing_ok and exc.filename in (root.name, "coord"):
                check()
                return unavailable, None, "root_missing" if exc.filename == root.name else "coord_missing"
            raise
    except TimeoutError:
        raise  # Expiry is never an ordinary unavailable continuation.
    except BaseException as exc:
        check()  # An absorbed alarm or slow failing I/O cannot renew C.
        return unavailable, _failure_error(exc), "refused"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", required=True, help="absolute supported interpreter")
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--exercise", choices=["baseline", "cancellation"], default="baseline")
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    output = args.artifacts.resolve()
    output.mkdir(parents=True, exist_ok=False)
    started = float(os.environ.get("E2E_STARTED_MONOTONIC", time.monotonic()))
    receipt = dict(schema=1, status="ENVIRONMENT_FAILURE", cleanup={"ok": False}, skipped=0,
                   variants={}, phases=[], source_sha=None, python=None,
                   exercise=args.exercise, signals=[])
    root = None
    root_identity = None
    owner = None
    action_failure = None
    cleanup_failure: dict[str, tuple] = {}
    export_failure: dict[str, tuple] = {}
    launcher_slots: dict[str, tuple] = {}
    controller_snapshot = dict(availability="unavailable", snapshot=None)
    diagnostic_read_observation = ("unknown", None)
    staging_incomplete = closure_incomplete = cleanup_incomplete = export_incomplete = False
    detail_complete = False
    final_result_committed = False
    # Detached fallback is selected before any optional acquisition. It never
    # references watcher memory; cancellation receives no diagnostic member.
    ownership_evidence = (dict(availability="unavailable", reason="not_frozen", data=None)
                          if args.exercise == "baseline" else None)
    ownership_frozen = False
    ownership_cleanup_returned = False
    ownership_budget = dict(clocks=0, stats=0, cycles=0, tokens=0)

    def latch_launcher(name: str, error: BaseException | None = None, *, category: str = "other") -> None:
        try:
            stage = "action" if name == "action" else "evidence" if name in {"collection", "export"} else "teardown"
            if error is None:
                at = time.monotonic()
                slot = (stage, category, at) if type(at) in (int, float) and 0 <= at <= 9007199254740991 and math.isfinite(at) else None
            else:
                slot = _failure_slot(stage, error)
            if slot is not None:
                launcher_slots.setdefault(name, slot)
        except BaseException:
            pass  # Diagnostic completion cannot replace any actual error.

    def failure_categories() -> dict:
        source_sha = receipt.get("source_sha")
        if not _failure_source(source_sha):
            return {}
        # Frozen validated controller state; export never rereads coord files.
        projection = dict(schema=1, source_sha=source_sha, controller=controller_snapshot,
                          launcher={name: _failure_object(launcher_slots.get(name)) for name in
                                    ("action", "owner", "diagnostic_read", "collection", "port", "root", "export")})
        result = dict(failure_categories=projection)
        try:
            state, outcome = diagnostic_read_observation
            # Closed frozen values only; no private read or observation at export.
            result["diagnostic_read_observation"] = dict(schema=1, source_sha=source_sha,
                                                        state=state, outcome=outcome)
        except BaseException:
            pass  # Omit the whole addition if optional projection fails.
        return result
    logs = []
    action_end, hard_end = started + 720, started + 900
    receipt["timing"] = dict(started=started, action_deadline=action_end,
                             hard_deadline=hard_end, phase="action")

    def interrupted(signum: int, _frame: object) -> None:
        phase = receipt["timing"]["phase"]
        if signum == signal.SIGALRM and phase != "action":
            receipt["timing"][phase]["timeout_signal"] = dict(signal=signum, at=time.monotonic(),
                                                              pid=os.getpid(), start=identity(os.getpid()), phase=phase)
            raise TimeoutError(f"{phase} deadline exhausted")
        receipt["signals"].append(dict(signal=signum, at=time.monotonic(),
                                       pid=os.getpid(), start=identity(os.getpid()), phase=phase))
        raise RuntimeError(f"runner interrupted by signal {signum}")

    # Register before dependencies, daemon, browser or any other child launch.
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGALRM):
        signal.signal(sig, interrupted)
    try:
        if not 0 <= time.monotonic() - started < 720:
            raise RuntimeError("invalid or exhausted cleanup-inclusive start clock")
        arm_deadline(action_end)
        receipt.update(daily_admission(source))
        if not (sys.platform == "linux" and os.environ.get("GITHUB_ACTIONS") == "true"
                and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
                and os.environ.get("RUNNER_OS") == "Linux"):
            raise RuntimeError("E2E requires an authorized disposable GitHub Ubuntu runner")
        if not Path(args.python).is_absolute():
            raise RuntimeError("interpreter must be absolute")
        version = subprocess.check_output([args.python, "-I", "-c", "import sys; print('.'.join(map(str,sys.version_info[:2])))"], text=True).strip()
        if version not in {"3.12", "3.14"}:
            raise RuntimeError("unsupported Python")
        receipt["python"] = version
        receipt["source_sha"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
        if subprocess.check_output(["git", "status", "--porcelain"], cwd=source, text=True):
            raise RuntimeError("clean committed source required")
        receipt["interpreter"] = dict(path=str(Path(args.python).resolve()),
                                      sha256=hashlib.sha256(Path(args.python).resolve().read_bytes()).hexdigest())
        node, npm, uv = (shutil.which(tool) for tool in ("node", "npm", "uv"))
        if not all((node, npm, uv)):
            raise RuntimeError("Node24/npm/uv required")
        node_version = subprocess.check_output([node, "--version"], text=True).strip()
        if not node_version.startswith("v24."):
            raise RuntimeError("Node major must be 24")
        receipt["node"] = dict(path=node, version=node_version)
        root = Path(tempfile.mkdtemp(prefix="happyranch-e2e-"))
        root.chmod(0o700)
        root_info = root.lstat()
        root_identity = (root_info.st_dev, root_info.st_ino)
        for name in ("home", "config", "cache", "data", "tmp", "daemon", "client", "coord", "bin", "witness", "raw"):
            (root / name).mkdir(mode=0o700)
        # Independent same-buffer launcher reads bracket ORIGINAL Owned setup.
        # The original initializer/signal capability check is unchanged.
        launcher_reads = []
        for bracket in (0, 1):
            if args.exercise == "baseline":
                ownership_budget["clocks"] += 1
                at = time.monotonic()
                observation = None
                if at < action_end:
                    ownership_budget["stats"] += 1
                    try:
                        with open(f"/proc/{os.getpid()}/stat", "rb", buffering=0) as stream:
                            raw = stream.read(4097)
                    except TimeoutError:
                        raise
                    except OSError:
                        pass
                    else:
                        if (0 < len(raw) <= 4096 and raw.endswith(b"\n")
                                and raw.startswith(str(os.getpid()).encode("ascii") + b" (") and b") " in raw):
                            tail = raw.rsplit(b") ", 1)[1].split()
                            if (len(tail) >= 20 and tail[0] in (b"R", b"S", b"D", b"Z", b"T", b"t", b"W", b"X", b"x", b"K", b"P", b"I")
                                    and 1 <= len(tail[1]) <= 20 and all(48 <= c <= 57 for c in tail[1]) and int(tail[1]) < 2**64
                                    and 1 <= len(tail[19]) <= 20 and 49 <= tail[19][0] <= 57
                                    and all(48 <= c <= 57 for c in tail[19]) and int(tail[19]) < 2**64):
                                observation = tail[19].decode("ascii")
                launcher_reads.append(observation)
            if bracket == 0:
                owner = Owned(root)
        if args.exercise == "baseline":
            owner.evidence_budget = ownership_budget
            ownership_budget["clocks"] += 1
            binding_at = time.monotonic()
            if (type(os.getpid()) is int and 0 < os.getpid() < 2**64
                    and launcher_reads[0] is not None and launcher_reads[0] == launcher_reads[1]
                    and binding_at < action_end
                    and all(type(receipt.get(key)) is str and len(receipt[key]) == 40
                            and all(c in "0123456789abcdef" for c in receipt[key])
                            for key in ("source_sha", "source_tree"))
                    and receipt["event"] in ("schedule", "workflow_dispatch")
                    and all(type(receipt.get(key)) is str and 1 <= len(receipt[key]) <= 20
                            and receipt[key][0] in "123456789" and all(c in "0123456789" for c in receipt[key])
                            for key in ("run_id", "run_attempt"))
                    and all(type(value) is int and 0 <= value < 2**64 for value in root_identity)
                    and root_identity[1] > 0):
                owner.evidence_binding = dict(
                    state="bound", reason="none", source_sha=receipt["source_sha"], source_tree=receipt["source_tree"],
                    event=receipt["event"], run_id=receipt["run_id"], run_attempt=receipt["run_attempt"], exercise="baseline",
                    root=dict(device=root_identity[0], inode=root_identity[1]),
                    launcher=dict(pid=os.getpid(), start=launcher_reads[0]), subreaper="initialized")
        env = {"HOME": str(root / "home"), "XDG_CONFIG_HOME": str(root / "config"),
               "XDG_CACHE_HOME": str(root / "cache"), "XDG_DATA_HOME": str(root / "data"),
               "TMPDIR": str(root / "tmp"), "TMP": str(root / "tmp"), "TEMP": str(root / "tmp"),
               "PATH": os.pathsep.join(dict.fromkeys([str(Path(node).parent), str(Path(uv).parent), "/usr/local/bin", "/usr/bin", "/bin"])),
               "LANG": "C.UTF-8", "E2E_ROOT": str(root), "CI": "1",
               "HAPPYRANCH_DAEMON_HOME": str(root / "daemon"), "HAPPYRANCH_DAEMON_PORT": "0",
               "HAPPYRANCH_WEB_DIST": str(source / "web" / "dist"),
               "UV_PROJECT_ENVIRONMENT": str(root / "venv"), "UV_PYTHON_DOWNLOADS": "never",
               "PLAYWRIGHT_BROWSERS_PATH": str(root / "browsers")}
        # Production systemd selection may need the user's runtime socket.
        # These contain paths only, and never substitute/force a backend.
        for key in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"):
            if key in os.environ:
                env[key] = os.environ[key]

        def command(name: str, argv: list[str], cwd: Path = source) -> None:
            begin = time.monotonic()
            cap = 480 if name == "scenarios" and args.exercise == "baseline" else 240
            end = min(begin + cap, action_end)
            if name == "scenarios":
                argv = [*argv, "--deadline", str(end)]
            arm_deadline(end)  # Reject exhaustion before opening logs or spawning.
            log = root / "raw" / (name + ".txt")
            logs.append(log)
            diagnostic = None
            launch_argv, launch_options = argv, {}  # Disabled fallback chosen BEFORE optional setup.
            try:
                if name == "scenarios":
                    diagnostic = _ScenarioDiagnostic(end)
                    if diagnostic.ready:
                        launch_argv = [*argv, "--_diagnostic-fd", str(diagnostic.descriptor)]
                        launch_options = {"pass_fds": (diagnostic.descriptor,)}
                with log.open("w") as stream:
                    arm_deadline(end)
                    try:
                        process = subprocess.Popen(launch_argv, cwd=cwd, env=env, stdout=stream,
                                                   stderr=subprocess.STDOUT, **launch_options)
                    finally:
                        if diagnostic is not None:
                            diagnostic.release_endpoint("sender")  # Before owner.add/wait, also on Popen failure.
                    owner.add(process.pid)
                    active_phase = dict(name=name, argv=launch_argv, pid=process.pid, exit=None,
                                        entered=begin, deadline=end, cap_seconds=cap, allowance_seconds=end - begin)
                    receipt["phases"].append(active_phase)
                    try:
                        if name == "scenarios" and args.exercise == "baseline":
                            # Numeric exclusion is recorded even if generation binding
                            # fails. Unknown numeric identity can never enable probes.
                            owner.evidence_phase_pid = process.pid if type(process.pid) is int and 0 < process.pid < 2**64 else None
                            owner.evidence_original_phase = active_phase
                            phase_reads = []
                            for bracket in (0, 1):
                                ownership_budget["clocks"] += 1
                                at = time.monotonic()
                                observation = None
                                if at < end and owner.evidence_phase_pid is not None:
                                    ownership_budget["stats"] += 1
                                    try:
                                        with open(f"/proc/{process.pid}/stat", "rb", buffering=0) as stream:
                                            raw = stream.read(4097)
                                    except TimeoutError:
                                        raise
                                    except OSError:
                                        pass
                                    else:
                                        if (0 < len(raw) <= 4096 and raw.endswith(b"\n")
                                                and raw.startswith(str(process.pid).encode("ascii") + b" (") and b") " in raw):
                                            tail = raw.rsplit(b") ", 1)[1].split()
                                            if (len(tail) >= 20 and tail[0] in (b"R", b"S", b"D", b"Z", b"T", b"t", b"W", b"X", b"x", b"K", b"P", b"I")
                                                    and 1 <= len(tail[1]) <= 20 and all(48 <= c <= 57 for c in tail[1]) and int(tail[1]) < 2**64
                                                    and 1 <= len(tail[19]) <= 20 and 49 <= tail[19][0] <= 57
                                                    and all(48 <= c <= 57 for c in tail[19]) and int(tail[19]) < 2**64):
                                                observation = (tail[19].decode("ascii"), int(tail[1]))
                                phase_reads.append(observation)
                            ownership_budget["clocks"] += 1
                            binding_at = time.monotonic()
                            if (owner.evidence_binding is not None and phase_reads[0] is not None
                                    and phase_reads[0] == phase_reads[1]
                                    and phase_reads[0] == (owner.pids.get(process.pid), owner.evidence_binding["launcher"]["pid"])
                                    and type(begin) in (int, float) and type(end) in (int, float)
                                    and 0 <= begin <= binding_at < end <= 9007199254740991
                                    and cap == 480 and end == min(begin + 480, action_end)):
                                owner.evidence_phase = dict(
                                    name="scenarios", allocation_state="bound", reason="none",
                                    controller=dict(pid=process.pid, start=phase_reads[0][0]), entered=begin, deadline=end,
                                    outcome="partial", exit=None, finished=None, seconds=None, interrupted=None)
                                owner.evidence_active_open = True  # Immutable allocation complete LAST.
                        while process.poll() is None:
                            if time.monotonic() >= end:
                                raise TimeoutError(f"{name} action deadline")
                            ready = root / "cancellation-ready.json"
                            if args.exercise == "cancellation" and ready.exists() and not (output / "active.json").exists():
                                witness = json.loads(ready.read_text())
                                owner.sample()
                                with owner.lock:
                                    if (owner.pids.get(witness["provider"]["pid"]) == witness["provider"]["pid_start"]
                                            and owner.units and owner.groups):
                                        active = dict(witness, launcher=dict(pid=os.getpid(), start=identity(os.getpid())),
                                                      owned=dict(identities=dict(owner.pids), units=sorted(owner.units),
                                                                 groups=sorted(owner.groups)), source_sha=receipt["source_sha"])
                                        temporary = output / "active.tmp"
                                        temporary.write_text(json.dumps(active, indent=2))
                                        temporary.replace(output / "active.json")
                            time.sleep(0.02)
                        code = process.returncode
                        active_phase["exit"] = code
                    finally:
                        if name == "scenarios" and args.exercise == "baseline":
                            owner.evidence_active_open = False
                        active_phase["finished"] = time.monotonic()
                        active_phase["seconds"] = active_phase["finished"] - begin
                        active_phase["interrupted"] = process.poll() is None
                        if name == "scenarios" and args.exercise == "baseline":
                            # This marker is reached only after ALL original phase
                            # assignments. No extra poll, decode or status transfer.
                            owner.evidence_terminal = True
                if diagnostic is not None:
                    diagnostic.receive()  # SAME command end/alarm, after actual exit/finalization.
            finally:
                if diagnostic is not None:
                    diagnostic.release()
            arm_deadline(action_end)
            if code:
                raise RuntimeError(f"{name} exited {code}")

        command("dependencies", [uv, "sync", "--frozen", "--python", args.python])
        python = str(root / "venv" / "bin" / "python")
        command("browser-dependencies", [uv, "pip", "install", "--python", python, "--require-hashes", "-r", str(source / "e2e" / "browser.lock")])
        command("browser-install", [python, "-I", "-m", "playwright", "install", "chromium"])
        npm_prefix = root / "cache" / "npm"
        npm_prefix.mkdir(mode=0o700)
        bridge = os.environ.get("E2E_NPM_DOWNLOAD_STORE")
        download_store = None
        if bridge is not None:
            if not bridge or bridge != str(Path(bridge)) or bridge.startswith("//"):
                raise ValueError("invalid runner npm download-store path")
            download_store = Path(bridge)
            copy_npm_downloads(download_store, npm_prefix / "_cacache", action_end)
        command("web-dependencies", [npm, "ci", "--cache", str(npm_prefix)], source / "web")
        if download_store is not None:
            copy_npm_downloads(npm_prefix / "_cacache", download_store, action_end)
        command("web-build", [npm, "run", "build"], source / "web")
        # Harness uses only stdlib + locked browser library; runtime lives in
        # separate real processes. No runtime/pytest import in the controller.
        command("scenarios", [python, "-I", str(source / "e2e" / "controller.py"),
                              "--root", str(root), "--source", str(source),
                              "--sha", receipt["source_sha"],
                              "--exercise", args.exercise])
        result = json.loads((root / "scenario.json").read_text())
        receipt.update(result)
        receipt["status"] = "PASS"
    except BaseException as exc:
        action_failure = (exc, exc.__traceback__)  # Before formatting or diagnostic work.
        latch_launcher("action", exc)
        receipt["failure_phase"] = "action"
        receipt["failure"] = repr(exc)
        receipt["traceback"] = traceback.format_exc()
    finally:
        action_finished = time.monotonic()
        receipt["timing"]["action"] = dict(finished=action_finished, deadline=action_end,
                                           allowance_seconds=720, elapsed_seconds=action_finished - started)
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, signal.SIG_IGN)
        staged: dict[str, bytes] = {}
        token = ""
        redaction_context: list[str] = []
        collection_window = "UNKNOWN"
        collection_diagnostic: dict[str, tuple] = {}

        def redact(text: str) -> str:
            if not token:
                raise ValueError("redaction context unavailable")
            for secret in [token, *redaction_context]:
                text = text.replace(secret, "[REDACTED]")
            return text

        def collect_evidence() -> str | None:
            nonlocal token, redaction_context, collection_window
            collection_window = "ROOT"
            if not root or not root.exists():
                return "other"
            # Capture once: partial removal must never erase trusted context.
            if not token:
                collection_window = "TOKEN_READ"
                try:
                    token = (root / "daemon" / "daemon.token").read_text().strip()
                except TimeoutError:
                    raise  # Reserve expiry is not an ordinary token-read failure.
                except (OSError, UnicodeError):
                    return "value"
            if not token:
                collection_window = "TOKEN_EMPTY"
                return "value"  # No raw text/receipt is safe without established context.
            context_path = root / "coord/redaction-context.json"
            collection_window = "CONTEXT_PRESENT"
            if context_path.exists():
                collection_window = "CONTEXT_READ"
                context_text = context_path.read_text()
                collection_window = "CONTEXT_DECODE"
                context = json.loads(context_text)
                collection_window = "CONTEXT_VALIDATE"
                if not isinstance(context, list) or not context or not all(isinstance(s, str) and s for s in context) or token not in context:
                    raise ValueError("invalid retained redaction context")
                redaction_context = context
            collection_window = "SCENARIO_PRESENT"
            if (root / "scenario.json").exists():
                collection_window = "SCENARIO_READ"
                scenario_text = (root / "scenario.json").read_text()
                collection_window = "SCENARIO_DECODE"
                result = json.loads(scenario_text)
                collection_window = "SCENARIO_MERGE"
                receipt.update({k: result[k] for k in ("variants", "scenario_phases", "e07") if k in result})
            collection_window = "RAW_ENUM"
            paths = iter((root / "raw").glob("*"))
            while True:
                collection_window = "RAW_ENUM"
                try:
                    path = next(paths)
                except StopIteration:
                    break
                if path.suffix in {".txt", ".json", ".jsonl"}:
                    collection_window = "TEXT_READ"
                    text = path.read_text(errors="replace")
                    collection_window = "REDACT"
                    staged[path.name] = redact(text).encode()
                elif path.suffix == ".png":
                    collection_window = "IMAGE_READ"
                    staged[path.name] = path.read_bytes()
            # Completion accounting does not relax or replace any reader oracle.
            collection_window = "REQUIRED"
            required = BASELINE_ARTIFACTS + [phase["name"] + ".txt" for phase in receipt["phases"]] if args.exercise == "baseline" else ["observations.json"]
            if (not (root / "scenario.json").exists()
                    or any(name not in staged or (name.endswith((".json", ".png")) and not staged[name]) for name in required)):
                return "other"
            collection_window = "UNKNOWN"  # Success is not a failed operation window.
            return None

        def reserve(name: str, cap: float, outer: float) -> dict:
            entered = time.monotonic()
            phase = dict(entered=entered, deadline=min(entered + cap, outer), ok=False)
            phase["allowance_seconds"] = max(0, phase["deadline"] - entered)
            receipt["timing"].update({"phase": name, name: phase})
            return phase

        cleanup = reserve("cleanup", 60, min(action_end + 60, hard_end))
        operations = {name: dict(attempted=False, complete=False) for name in
                      ("owner", "diagnostic_read", "collection", "port_list", "ports", "root", "absence")}
        owner_ok = False
        current_attempt = "owner"

        def cleanup_time() -> None:
            if time.monotonic() >= cleanup["deadline"]:
                raise TimeoutError("cleanup deadline exhausted")

        def incomplete(name: str, error: BaseException | None = None, *, category: str = "other") -> tuple | None:
            nonlocal staging_incomplete, closure_incomplete, cleanup_incomplete
            if error is not None and receipt["timing"]["phase"] == "cleanup":
                cleanup_failure.setdefault("first", (error, error.__traceback__))
            cleanup_incomplete = True
            first_collection = name == "collection" and not staging_incomplete
            if name == "collection":
                staging_incomplete = True
            elif name in {"owner", "port", "root"}:
                closure_incomplete = True
            latch_launcher(name, error, category=category)
            # Sticky reservation above is FIRST. An interrupted/unreached claim
            # cannot be filled by recollection: staging_incomplete already owns it.
            if first_collection:
                claim = (receipt["timing"]["phase"], collection_window, "F" if error is not None else "U", object())
                if collection_diagnostic.setdefault("claim", claim) is claim:
                    return claim
            return None

        def observe_collection(claim: tuple | None) -> None:
            if claim is None or collection_diagnostic.get("claim") is not claim or "output" in collection_diagnostic:
                return
            collection_diagnostic["output"] = claim  # Charge before eligibility/write.
            phase, window, kind, _ = claim
            windows = ("ROOT", "TOKEN_READ", "TOKEN_EMPTY", "CONTEXT_PRESENT", "CONTEXT_READ", "CONTEXT_DECODE",
                       "CONTEXT_VALIDATE", "SCENARIO_PRESENT", "SCENARIO_READ", "SCENARIO_DECODE", "SCENARIO_MERGE",
                       "RAW_ENUM", "TEXT_READ", "REDACT", "IMAGE_READ", "REQUIRED", "UNKNOWN")
            if phase not in ("cleanup", "export") or window not in windows or kind not in ("F", "U"):
                return
            end = receipt["timing"][phase]["deadline"] - (1 if phase == "export" else 0)
            if time.monotonic() >= end:
                return
            line = ("HRD1:C:" + phase.upper() + ":" + window + ":" + kind + "\n").encode("ascii")
            _diagnostic_write(line, end)

        def stage_evidence(check: Callable[[], None]) -> None:
            nonlocal detail_complete
            check()
            try:
                missing = collect_evidence()
            except BaseException as exc:
                if receipt["timing"]["phase"] == "export":
                    export_failure.setdefault("first", (exc, exc.__traceback__))
                claim = incomplete("collection", exc)
                observe_collection(claim)  # Outside the collector's catch scope.
                raise
            else:
                if missing is not None:
                    claim = incomplete("collection", category=missing)
                    observe_collection(claim)
                else:
                    detail_complete = True
            check()

        try:
            arm_deadline(cleanup["deadline"])
            operations["owner"]["attempted"] = True
            try:
                if owner is not None and args.exercise == "baseline":
                    owner.evidence_active_open = False
                    original_phase = owner.evidence_original_phase
                    phase = owner.evidence_phase
                    if phase is None:
                        phase = dict(name="scenarios", allocation_state="unbound", reason="unreached",
                                     controller=None, entered=None, deadline=None, outcome="unreached",
                                     exit=None, finished=None, seconds=None, interrupted=None)
                    if original_phase is not None:
                        # Main copies only already-assigned ORIGINAL fields. A
                        # partial finalizer never becomes terminal from its exit.
                        phase = dict(phase)
                        malformed = False
                        for key in ("exit", "finished", "seconds", "interrupted"):
                            value = original_phase.get(key)
                            valid = (value is None or (type(value) is int and -(2**31) <= value < 2**31)) if key == "exit" else (value is None or type(value) is bool) if key == "interrupted" else (value is None or type(value) in (int, float) and 0 <= value <= 9007199254740991)
                            if not valid:
                                malformed = True
                            phase[key] = value if valid else None
                        if (phase["finished"] is not None and phase["seconds"] is not None
                                and phase["entered"] is not None
                                and (phase["finished"] < phase["entered"] or phase["seconds"] != phase["finished"] - phase["entered"])):
                            malformed = True
                        if malformed:
                            phase.update(outcome="malformed", exit=None, finished=None, seconds=None, interrupted=None)
                        elif (phase["allocation_state"] == "bound" and owner.evidence_terminal and phase["finished"] is not None and phase["seconds"] is not None
                              and phase["interrupted"] is False and phase["exit"] is not None):
                            phase["outcome"] = "terminal"
                        elif (phase["allocation_state"] == "bound" and owner.evidence_terminal and phase["finished"] is not None and phase["seconds"] is not None
                              and phase["interrupted"] is True and phase["exit"] is None):
                            phase["outcome"] = "interrupted"
                        else:
                            phase["outcome"] = "partial"
                    owner.evidence_phase = phase
                    # This marker is consumed by main's ORIGINAL first sample.
                    owner.evidence_entry = (cleanup["entered"], cleanup["deadline"])
                receipt["cleanup"] = owner.cleanup() if owner else {"ok": False, "reason": "setup did not admit containment"}
                ownership_cleanup_returned = owner is not None
                owner_ok = receipt["cleanup"]["ok"] is True
                operations["owner"]["complete"] = owner_ok
                if not owner_ok:
                    incomplete("owner")
            except TimeoutError as exc:
                incomplete("owner", exc)
                raise
            except BaseException as exc:
                incomplete("owner", exc)
            finally:
                # ONE freeze decision under the SAME original cleanup end,
                # before collection/root loss. Unknown/live watcher => no slot
                # inspection at all. Claim precedes work; completion is last.
                if args.exercise == "baseline" and not ownership_frozen:
                    ownership_frozen = True
                    ownership_evidence = dict(availability="unavailable", reason="interrupted", data=None)
                    if owner is not None:
                        owner.evidence_writes_ended = True
                    freeze_reason = "watcher_unknown"
                    try:
                        original_cleanup = receipt["cleanup"]
                        if (ownership_cleanup_returned and type(original_cleanup) is dict and original_cleanup.get("watcher_stopped") is False):
                            freeze_reason = "watcher_active"
                        elif (ownership_cleanup_returned and owner is not None and type(original_cleanup) is dict
                              and original_cleanup.get("watcher_stopped") is True):
                            if not owner.evidence_writes_ended:
                                freeze_reason = "main_writes_open"
                            elif ownership_budget["clocks"] >= 512:
                                freeze_reason = "no_budget"
                            else:
                                ownership_budget["clocks"] += 1
                                freeze_at = time.monotonic()
                                if not cleanup["entered"] <= freeze_at < cleanup["deadline"]:
                                    freeze_reason = "no_budget"
                                elif owner.evidence_binding is None:
                                    freeze_reason = "malformed"
                                else:
                                    freeze_reason = "malformed"
                                    binding = owner.evidence_binding
                                    phase = owner.evidence_phase
                                    slots = {}
                                    for name in ("active", "precleanup"):
                                        slot = dict(owner.evidence_slots[name])
                                        if slot["state"] == "claimed":
                                            slot.update(state="incomplete", reason="incomplete", targets=None,
                                                        launcher_a=None, launcher_b=None, controller_a=None, controller_b=None)
                                        slots[name] = slot
                                    returns = dict(owner.evidence_returns)
                                    if returns["state"] == "claimed":
                                        returns.update(state="incomplete", reason="interrupted", outcome="interrupted", coverage="partial")
                                    candidate = dict(availability="available", reason="none", data=dict(
                                        binding=binding, phase=phase, active=slots["active"], precleanup=slots["precleanup"],
                                        returns=returns, freeze=dict(state="frozen", watcher="stopped", writes="ended")))
                                    # Closed finite grammar, independently checked before
                                    # any serialization. No raw/private string is admitted.
                                    reasons = ("none", "unreached", "incomplete", "interrupted", "excluded", "not_registered", "unbound", "mismatch", "reused", "malformed", "absent", "unsupported", "io_error", "no_budget", "overflow", "reentry", "off_owner", "echild", "close_unknown", "cycle_limit")
                                    binding_reasons = ("none", "unreached", "incomplete", "interrupted", "mismatch", "malformed", "budget")
                                    states = ("unattempted", "claimed", "incomplete", "refused", "complete")
                                    grammar = {
                                        "member": dict(availability=("available",), reason=("none",), data="data"),
                                        "data": dict(binding="binding", phase="phase", active="slot", precleanup="slot", returns="returns", freeze="freeze"),
                                        "binding": dict(state=("bound",), reason=("none",), source_sha="H", source_tree="H", event=("schedule", "workflow_dispatch"), run_id="D", run_attempt="D", exercise=("baseline",), root="root", launcher="pin", subreaper=("initialized",)),
                                        "root": dict(device="U", inode="P"),
                                        "pin": dict(pid="P", start="D"),
                                        "phase": dict(name=("scenarios",), allocation_state=("unbound", "bound", "refused"), reason=binding_reasons, controller="?pin", entered="?T", deadline="?T", outcome=("unreached", "partial", "terminal", "interrupted", "malformed"), exit="?E", finished="?T", seconds="?T", interrupted="?B"),
                                        "slot": dict(state=states, reason=reasons, owner=(None, "watcher", "main"), entered="?T", finished="?T", deadline="?T", join=("unknown", "bound_live", "bound_interrupted_live", "bound_terminal_absent", "bound_terminal_observed", "bound_outcome_unknown", "never_bound", "reused", "mismatch", "malformed"), launcher_a="?stat", launcher_b="?stat", controller_a="?stat", controller_b="?stat", targets="?targets"),
                                        "stat": dict(outcome=("observed", "absent", "unreadable", "interrupted", "empty", "partial", "oversized", "malformed", "unattempted"), at="?T", start="?D", state=(None, "R", "S", "D", "Z", "T", "t", "W", "X", "x", "K", "P", "I"), ppid="?U"),
                                        "target": dict(pid="P", start="?D", kind=("direct_phase", "phase_number_excluded", "nonphase"), registration=("matched", "unregistered", "reused"), a="?stat", b="?stat", wait="wait", close=("not_opened", "closed", "unknown")),
                                        "wait": dict(state=states, reason=reasons, outcome=("unknown", "no_exit_state", "ineligible", "eligible_exit"), at="?T", code=(None, "CLD_EXITED", "CLD_KILLED", "CLD_DUMPED"), status="?N"),
                                        "returns": dict(state=states, reason=("none", "unreached", "incomplete", "interrupted", "malformed", "annotation_limit", "no_budget"), outcome=("unreached", "returned", "interrupted", "unknown"), rows="?rows", coverage=("none", "all_reached", "partial")),
                                        "return": dict(index="index", pid="P", wait_status="W", exit="?E", generation="?D", join=("qualified", "unknown"), slot=(None, "precleanup"), target="?index0"),
                                        "freeze": dict(state=("frozen",), watcher=("stopped",), writes=("ended",)),
                                    }
                                    pending_fields = [(candidate, "member")]
                                    valid = True
                                    visits = 0
                                    while pending_fields:
                                        value, domain = pending_fields.pop()
                                        visits += 1
                                        if visits > 4096:
                                            valid = False
                                            break
                                        if type(domain) is tuple:
                                            if not any(type(value) is type(option) and value == option for option in domain):
                                                valid = False
                                                break
                                            continue
                                        if domain.startswith("?"):
                                            if value is None:
                                                continue
                                            domain = domain[1:]
                                        if domain in grammar:
                                            shape = grammar[domain]
                                            if type(value) is not dict or value.keys() != shape.keys():
                                                valid = False
                                                break
                                            pending_fields.extend((value[key], field) for key, field in shape.items())
                                        elif domain in ("targets", "rows"):
                                            limit = 17 if domain == "targets" else 34
                                            if type(value) is not list or len(value) > limit:
                                                valid = False
                                                break
                                            pending_fields.extend((item, "target" if domain == "targets" else "return") for item in value)
                                        else:
                                            if domain in ("U", "P", "E", "W", "N", "index", "index0"):
                                                lower, upper = {"U": (0, 2**64-1), "P": (1, 2**64-1), "E": (-(2**31), 2**31-1), "W": (0, 2**32-1), "N": (0, 2**31-1), "index": (1, 34), "index0": (0, 16)}[domain]
                                                good = type(value) is int and lower <= value <= upper
                                            elif domain == "T":
                                                good = type(value) in (int, float) and 0 <= value <= 9007199254740991
                                            elif domain == "B":
                                                good = type(value) is bool
                                            elif domain == "D":
                                                good = (type(value) is str and 1 <= len(value) <= 20 and value[0] in "123456789"
                                                        and all(c in "0123456789" for c in value))
                                            elif domain == "H":
                                                good = type(value) is str and len(value) == 40 and all(c in "0123456789abcdef" for c in value)
                                            else:
                                                good = False
                                            if not good:
                                                valid = False
                                                break
                                    if not valid:
                                        raise ValueError("ownership shape refused")
                                    if (binding is not owner.evidence_binding or phase is not owner.evidence_phase
                                            or binding["root"] != dict(device=root_identity[0], inode=root_identity[1])
                                            or any(binding[key] != receipt[key] for key in ("source_sha", "source_tree", "event", "run_id", "run_attempt"))):
                                        raise ValueError("ownership binding refused")
                                    if phase["allocation_state"] == "bound":
                                        if (phase["reason"] != "none" or phase["controller"] is None
                                                or phase["controller"]["pid"] != owner.evidence_phase_pid
                                                or phase["entered"] is None or phase["deadline"] is None
                                                or not phase["entered"] <= phase["deadline"] == min(phase["entered"] + 480, action_end)):
                                            raise ValueError("ownership phase refused")
                                    elif (phase["reason"] == "none" or any(phase[key] is not None for key in ("controller", "entered", "deadline"))
                                          or phase["outcome"] not in ("unreached", "partial", "malformed")):
                                        raise ValueError("ownership unbound phase refused")
                                    if phase["outcome"] in ("terminal", "interrupted"):
                                        if (phase["allocation_state"] != "bound" or not owner.evidence_terminal
                                                or phase["finished"] is None or phase["seconds"] is None
                                                or phase["finished"] < phase["entered"] or phase["seconds"] != phase["finished"] - phase["entered"]
                                                or (phase["outcome"] == "terminal" and (phase["exit"] is None or phase["interrupted"] is not False))
                                                or (phase["outcome"] == "interrupted" and (phase["exit"] is not None or phase["interrupted"] is not True))):
                                            raise ValueError("ownership outcome refused")
                                    elif phase["outcome"] in ("unreached", "malformed") and any(phase[key] is not None for key in ("exit", "finished", "seconds", "interrupted")):
                                        raise ValueError("ownership null outcome refused")
                                    for name, slot in slots.items():
                                        if slot["state"] != "complete":
                                            if slot["reason"] == "none" or any(slot[key] is not None for key in ("launcher_a", "launcher_b", "controller_a", "controller_b", "targets")):
                                                raise ValueError("ownership incomplete slot refused")
                                            if slot["state"] == "unattempted" and (slot["reason"] != "unreached" or slot["join"] != "unknown" or any(slot[key] is not None for key in ("owner", "entered", "finished", "deadline"))):
                                                raise ValueError("ownership unattempted slot refused")
                                            continue
                                        if (slot["reason"] != "none" or slot["owner"] != ("watcher" if name == "active" else "main")
                                                or phase["allocation_state"] != "bound"
                                                or any(slot[key] is None for key in ("entered", "finished", "deadline", "launcher_a", "launcher_b", "controller_a", "controller_b", "targets"))
                                                or not slot["entered"] <= slot["finished"] <= slot["deadline"]
                                                or slot["deadline"] != (phase["deadline"] if name == "active" else cleanup["deadline"])
                                                or slot["entered"] < (phase["entered"] if name == "active" else cleanup["entered"])
                                                or (name == "precleanup" and phase["finished"] is not None and slot["entered"] < phase["finished"])):
                                            raise ValueError("ownership slot interval refused")
                                        if not slot["launcher_a"]["at"] <= slot["controller_a"]["at"] <= slot["controller_b"]["at"] <= slot["launcher_b"]["at"]:
                                            raise ValueError("ownership context order refused")
                                        for key in ("launcher_a", "launcher_b"):
                                            row = slot[key]
                                            if row["outcome"] != "observed" or row["start"] != binding["launcher"]["start"]:
                                                raise ValueError("ownership launcher bracket refused")
                                        a, b = slot["controller_a"], slot["controller_b"]
                                        live = all(row["outcome"] == "observed" and row["start"] == phase["controller"]["start"] and row["state"] not in ("Z", "X", "x") for row in (a, b))
                                        if any(row["outcome"] == "observed" and row["start"] != phase["controller"]["start"] for row in (a, b)):
                                            raise ValueError("ownership reused controller refused")
                                        join = slot["join"]
                                        if ((join == "bound_live" and (name != "active" or not live))
                                                or (join == "bound_interrupted_live" and (phase["outcome"] != "interrupted" or not live))
                                                or (join == "bound_terminal_absent" and (phase["outcome"] != "terminal" or any(row["outcome"] != "absent" for row in (a, b))))
                                                or (join == "bound_terminal_observed" and (phase["outcome"] != "terminal" or any(row["outcome"] != "observed" or row["state"] not in ("Z", "X", "x") for row in (a, b))))
                                                or join not in ("bound_live", "bound_interrupted_live", "bound_terminal_absent", "bound_terminal_observed", "bound_outcome_unknown")
                                                or (join == "bound_outcome_unknown" and phase["outcome"] == "terminal")):
                                            raise ValueError("ownership phase join refused")
                                        seen_pids = set()
                                        stats = [slot[key] for key in ("launcher_a", "launcher_b", "controller_a", "controller_b")]
                                        for target in slot["targets"]:
                                            if target["pid"] in seen_pids:
                                                raise ValueError("ownership duplicate target refused")
                                            seen_pids.add(target["pid"])
                                            a, b, wait = target["a"], target["b"], target["wait"]
                                            if a is None or b is None or not slot["controller_a"]["at"] <= a["at"] <= b["at"] <= slot["controller_b"]["at"]:
                                                raise ValueError("ownership target interval refused")
                                            stats.extend((a, b))
                                            excluded = target["pid"] == owner.evidence_phase_pid
                                            if excluded != (target["kind"] != "nonphase") or (excluded and (wait["state"] != "unattempted" or wait["reason"] != "excluded" or target["close"] != "not_opened")):
                                                raise ValueError("ownership direct exclusion refused")
                                            if (target["kind"] == "direct_phase" and target["start"] != phase["controller"]["start"]
                                                    or target["registration"] == "unregistered" and target["start"] is not None):
                                                raise ValueError("ownership registration refused")
                                            if wait["at"] is not None and not a["at"] <= wait["at"] <= b["at"]:
                                                raise ValueError("ownership wait interval refused")
                                            if wait["state"] == "complete":
                                                if (excluded or target["start"] is None or target["registration"] != "matched" or target["close"] != "closed"
                                                        or wait["at"] is None or any(row["outcome"] != "observed" or row["start"] != target["start"] or row["ppid"] != binding["launcher"]["pid"] for row in (a, b))):
                                                    raise ValueError("ownership wait bracket refused")
                                                if wait["outcome"] == "eligible_exit":
                                                    if (wait["reason"] != "none" or wait["code"] is None or wait["status"] is None
                                                            or (wait["code"] == "CLD_EXITED" and wait["status"] > 255)
                                                            or (wait["code"] != "CLD_EXITED" and wait["status"] == 0)):
                                                        raise ValueError("ownership eligible exit refused")
                                                elif (wait["outcome"], wait["reason"], wait["code"], wait["status"]) not in (("no_exit_state", "none", None, None), ("ineligible", "echild", None, None)):
                                                    raise ValueError("ownership empty wait refused")
                                            elif wait["reason"] == "none" or wait["outcome"] != "unknown" or wait["code"] is not None or wait["status"] is not None:
                                                raise ValueError("ownership unqualified wait refused")
                                        for row in stats:
                                            if row["at"] is None or not slot["entered"] <= row["at"] <= slot["finished"]:
                                                raise ValueError("ownership stat time refused")
                                            if row["outcome"] == "observed":
                                                if row["start"] is None or row["state"] is None or row["ppid"] is None:
                                                    raise ValueError("ownership stat fields refused")
                                            elif any(row[key] is not None for key in ("start", "state", "ppid")):
                                                raise ValueError("ownership stat nulls refused")
                                    if returns["state"] == "complete":
                                        if (returns["reason"], returns["outcome"], returns["coverage"]) != ("none", "returned", "all_reached") or returns["rows"] is None:
                                            raise ValueError("ownership return coverage refused")
                                    elif returns["state"] == "unattempted":
                                        if (returns["reason"], returns["outcome"], returns["coverage"], returns["rows"]) != ("unreached", "unreached", "none", None):
                                            raise ValueError("ownership unreached returns refused")
                                    elif returns["state"] == "incomplete":
                                        if returns["reason"] == "none" or returns["coverage"] != "partial" or returns["outcome"] not in ("interrupted", "unknown"):
                                            raise ValueError("ownership partial returns refused")
                                    else:
                                        raise ValueError("ownership returns refused")
                                    for index, row in enumerate(returns["rows"] or []):
                                        if row["index"] != index + 1:
                                            raise ValueError("ownership return order refused")
                                        if row["join"] == "qualified":
                                            slot = slots["precleanup"]
                                            if returns["state"] != "complete" or slot["state"] != "complete" or row["slot"] != "precleanup" or row["target"] is None or row["target"] >= len(slot["targets"]):
                                                raise ValueError("ownership return pin refused")
                                            target = slot["targets"][row["target"]]
                                            if (row["pid"] != target["pid"] or row["generation"] != target["start"] or target["wait"]["outcome"] != "eligible_exit"
                                                    or row["pid"] == owner.evidence_phase_pid or row["generation"] is None):
                                                raise ValueError("ownership return generation refused")
                                        elif any(row[key] is not None for key in ("generation", "slot", "target")):
                                            raise ValueError("ownership unknown return refused")
                                    # Grammar and joins are checked before this bounded
                                    # fresh-container detach. No watcher/main writer remains.
                                    detached = json.loads(json.dumps(candidate, allow_nan=False))
                                    formatted = json.dumps({"ownership_evidence": detached}, indent=2, allow_nan=False).encode("utf-8")
                                    parts = [len(json.dumps({"ownership_evidence": {"data": {name: detached["data"][name]}}}, indent=2).encode("utf-8")) for name in ("active", "precleanup")]
                                    rest = {key: value for key, value in detached["data"].items() if key not in ("active", "precleanup")}
                                    rest_size = len(json.dumps({"ownership_evidence": dict(detached, data=rest)}, indent=2).encode("utf-8"))
                                    freeze_reason = "format_refused"
                                    # Wrapper includes both braces/newlines; +2 covers
                                    # the actual comma/separator when other members exist.
                                    if len(formatted) + 2 <= 32768 and max(parts) <= 8192 and rest_size <= 16384:
                                        if ownership_budget["clocks"] >= 512:
                                            freeze_reason = "no_budget"
                                        else:
                                            ownership_budget["clocks"] += 1
                                            if time.monotonic() >= cleanup["deadline"]:
                                                freeze_reason = "no_budget"
                                            else:
                                                ownership_evidence = detached  # Complete LAST; no later upgrade.
                                                freeze_reason = "none"
                    except TimeoutError:
                        freeze_reason = "interrupted"
                        raise
                    except (ValueError, TypeError, KeyError, OverflowError):
                        freeze_reason = "malformed"
                    except BaseException:
                        freeze_reason = "interrupted"
                        raise
                    finally:
                        if freeze_reason != "none":
                            ownership_evidence = dict(availability="unavailable", reason=freeze_reason, data=None)
            cleanup_time()  # Even a caught ordinary error may have exhausted C.

            current_attempt = "diagnostic_read"
            operations["diagnostic_read"]["attempted"] = True
            try:
                try:
                    cleanup_time()
                    diagnostic_read_observation = ("admitted", None)
                except BaseException:
                    pass  # Optional history cannot replace the actual read/error.
                controller_snapshot, read_error, read_outcome = _read_failure_categories(
                    root, receipt["source_sha"], cleanup_time, "failure_phase" in receipt)
                try:
                    cleanup_time()
                    diagnostic_read_observation = ("completed", read_outcome)
                except BaseException:
                    pass  # Mandatory snapshot/error assignment above still stands.
                operations["diagnostic_read"]["complete"] = read_error is None
                if read_error is not None:
                    incomplete("diagnostic_read", category=read_error)
            except TimeoutError as exc:
                incomplete("diagnostic_read", exc)
                raise
            cleanup_time()

            current_attempt = "collection"
            operations["collection"]["attempted"] = True
            try:
                stage_evidence(cleanup_time)
                operations["collection"]["complete"] = detail_complete and not staging_incomplete
            except TimeoutError:
                raise
            except BaseException:
                pass  # Already latched; remaining existing teardown is independent.
            cleanup_time()

            current_attempt = "port"
            operations["port_list"]["attempted"] = True
            ports = None
            try:
                cleanup_time()
                if root is None:
                    incomplete("port")
                else:
                    # Missing metadata is unknown, never an invented empty list.
                    ports = json.loads((root / "ports.json").read_text())
                    if type(ports) is not list or any(type(port) is not int or not 0 < port < 65536 for port in ports):
                        raise ValueError("invalid port observation")
                    operations["port_list"]["complete"] = True
            except TimeoutError as exc:
                incomplete("port", exc)
                raise
            except BaseException as exc:
                incomplete("port", exc)
            cleanup_time()
            if ports is not None and operations["port_list"]["complete"]:
                absence = {}
                receipt["cleanup"]["ports_absent"] = absence
                operations["ports"]["attempted"] = True
                for port in ports:
                    cleanup_time()
                    try:
                        with socket.socket() as sock:
                            cleanup_time()
                            sock.settimeout(min(1, cleanup["deadline"] - time.monotonic()))
                            absence[str(port)] = sock.connect_ex(("127.0.0.1", port)) != 0
                        if not absence[str(port)]:
                            incomplete("port")
                    except TimeoutError as exc:
                        incomplete("port", exc)
                        # An individual ordinary 1s probe timeout may continue
                        # only if the original overall allowance is still live.
                    except BaseException as exc:
                        incomplete("port", exc)
                    cleanup_time()
                operations["ports"]["complete"] = len(absence) == len(set(ports)) and all(absence.values())
            cleanup_time()

            current_attempt = "root"
            operations["root"]["attempted"] = True
            try:
                if root is None or root_identity is None:
                    incomplete("root")
                else:
                    with _failure_directories(root, cleanup_time, coord=False) as (directory, verify):
                        verify()
                        observed = os.fstat(directory)
                        if (observed.st_dev, observed.st_ino) != root_identity:
                            raise ValueError("owned root identity changed")
                        cleanup_time()
                        shutil.rmtree(root)  # ONE existing attempt, never export rescue.
                        cleanup_time()
                    operations["root"]["complete"] = True
            except TimeoutError as exc:
                incomplete("root", exc)
                raise
            except BaseException as exc:
                incomplete("root", exc)
            cleanup_time()
            operations["absence"]["attempted"] = True
            try:
                if root is not None:
                    with _failure_directories(root.parent, cleanup_time, coord=False, owned=False) as (parent, verify):
                        verify()
                        cleanup_time()
                        try:
                            os.stat(root.name, dir_fd=parent, follow_symlinks=False)
                        except FileNotFoundError:
                            absent = True  # Literal final ENOENT, pinned safe parent.
                        else:
                            absent = False
                        verify()
                        cleanup_time()
                    receipt["cleanup"].update(data_absent=absent, sockets_absent=absent)
                    operations["absence"]["complete"] = absent
                    if not absent:
                        incomplete("root")
                else:
                    incomplete("root")
            except TimeoutError as exc:
                incomplete("root", exc)
                raise
            except BaseException as exc:
                incomplete("root", exc)
            cleanup_time()
        except BaseException as exc:
            incomplete(current_attempt, exc)
            try:
                cleanup_time()
                if (current_attempt == "owner" and not operations["diagnostic_read"]["attempted"]
                        and controller_snapshot == dict(availability="unavailable", snapshot=None)
                        and launcher_slots.get("diagnostic_read") is None):
                    diagnostic_read_observation = ("unreached", None)
            except BaseException:
                pass  # Expiry/interruption retains the prior tuple; no rescue.
        finally:
            cleanup["finished"] = time.monotonic()
            cleanup["elapsed_seconds"] = cleanup["finished"] - cleanup["entered"]
            if (cleanup["finished"] >= cleanup["deadline"]
                    or not all(state["complete"] for state in operations.values())):
                cleanup_incomplete = True
            cleanup["ok"] = owner_ok and not (staging_incomplete or closure_incomplete or cleanup_incomplete)
            receipt["cleanup"]["ok"] = cleanup["ok"]
            if cleanup_failure:
                first = cleanup_failure["first"][0]
                cleanup["error"] = type(first).__name__
                try:
                    receipt["cleanup"].setdefault("error", repr(first))
                except BaseException:
                    pass
            if not cleanup["ok"]:
                receipt["status"] = "FAIL"
                receipt.setdefault("failure_phase", "cleanup")
                # No new sample: preserve original observations, not absence.
                if owner:
                    receipt["cleanup"].setdefault("identities", owner.pids.copy())
                    receipt["cleanup"].setdefault("observed_units", sorted(owner.units.copy()))
                    receipt["cleanup"].setdefault("observed_groups", sorted(owner.groups.copy()))

        export = reserve("export", 120, hard_end)

        def publish(row: dict, *, final: bool = False) -> None:
            nonlocal final_result_committed
            temporary = output / "result.tmp"
            destination = output / "result.json"
            temporary.write_text(json.dumps(row, indent=2))
            if not final:
                if time.monotonic() >= export["deadline"]:
                    raise TimeoutError("export deadline exhausted")
                temporary.replace(destination)
                return
            # Finish all fallible preparation before the final admission check.
            # Retire SIGALRM process-wide, including any queued alarm. A thread
            # mask alone cannot cover an alarm delivered to a surviving watcher.
            # Disarming and the SAME original E-1 check are both precommit.
            signal.signal(signal.SIGALRM, signal.SIG_IGN)
            signal.setitimer(signal.ITIMER_REAL, 0)
            export_time()
            os.replace(temporary, destination)
            final_result_committed = True
            # No postcommit deadline check: an admitted OS call can overrun or
            # a crash can interrupt it. This is not hard timing/crash durability.

        def failure_receipt() -> dict:
            # Bounded metadata only: no raw exception/token text or partial PASS.
            closure = receipt["cleanup"]
            safe_cleanup = {key: closure[key] for key in
                            ("identities", "groups", "ports_absent", "data_absent", "sockets_absent", "watcher_stopped")
                            if key in closure}
            safe_cleanup.update(ok=False, observed_units=closure.get("observed_units", sorted(closure.get("units", {}))),
                                observed_groups=closure.get("observed_groups", sorted(closure.get("groups", {}))))
            binding = {key: receipt[key] for key in ADMISSION_FIELDS + ("interpreter", "node") if key in receipt}
            safe_timing = dict(receipt["timing"], cleanup=dict(receipt["timing"]["cleanup"], ok=False))
            return dict(binding, **failure_categories(), schema=1, status="FAIL", source_sha=receipt["source_sha"],
                        python=receipt["python"], exercise=args.exercise, skipped=0,
                        variants={}, signals=receipt["signals"], phases=receipt["phases"],
                        cleanup=safe_cleanup, timing=safe_timing,
                        failure_phase=receipt.get("failure_phase", "export"),
                        failure="incomplete detailed evidence",
                        total_seconds=time.monotonic() - started,
                        **({"ownership_evidence": ownership_evidence} if ownership_evidence is not None else {}))

        initial_fail_published = False

        def export_time() -> None:
            if time.monotonic() >= export["deadline"] - 1:
                raise TimeoutError("export deadline exhausted")

        try:
            arm_deadline(export["deadline"])
            publish(failure_receipt())
            initial_fail_published = True
            # Keep the final second of this SAME allowance for a failure receipt.
            arm_deadline(export["deadline"] - 1)
            if root and root.exists():
                # Preserve safe partial staging even if recollection fails.
                for name, content in staged.items():
                    export_time()
                    (output / name).write_bytes(content)
                    export_time()
                try:
                    stage_evidence(export_time)  # Read-only diagnostics; never teardown.
                except BaseException as exc:
                    export_failure.setdefault("first", (exc, exc.__traceback__))
                    latch_launcher("export", exc)
                    raise
            export_time()
            for name, content in staged.items():
                export_time()
                (output / name).write_bytes(content)
                export_time()
            if not detail_complete or staging_incomplete:
                raise ValueError("incomplete detailed evidence")
            if not token:
                raise ValueError("redaction context unavailable")
            if receipt["status"] == "PASS" and args.exercise == "baseline":
                required = BASELINE_ARTIFACTS + [phase["name"] + ".txt" for phase in receipt["phases"]]
                if any(name not in staged or (name.endswith((".json", ".png")) and not staged[name]) for name in required):
                    raise ValueError("missing/empty baseline evidence")
            manifest = json.loads((source / "e2e" / "manifest.json").read_text())
            export_time()
            export["finished"] = time.monotonic()
            export["elapsed_seconds"] = export["finished"] - export["entered"]
            export["ok"] = not export_incomplete
            receipt["evidence_seconds"] = export["elapsed_seconds"]
            receipt["total_seconds"] = export["finished"] - started
            receipt["target_exceeded"] = receipt["total_seconds"] > 600
            if (args.exercise != "baseline" or receipt["signals"] or not cleanup["ok"]
                    or set(receipt["variants"]) != set(manifest["variants"])
                    or any(row["status"] != "PASS" for row in receipt["variants"].values())):
                receipt["status"] = "FAIL"
            if receipt["status"] == "PASS":
                validate_timing(receipt)
            receipt.update(failure_categories())
            export_time()
            detailed = json.loads(redact(json.dumps(receipt)))
            if ownership_evidence is not None:
                detailed["ownership_evidence"] = ownership_evidence
            publish(detailed, final=True)
        except BaseException as exc:
            if not final_result_committed:
                receipt["status"] = "FAIL"
                export_failure.setdefault("first", (exc, exc.__traceback__))
                export_incomplete = True
                latch_launcher("export", exc)
                export["ok"] = False
                export["error"] = type(export_failure["first"][0]).__name__
                export["finished"] = time.monotonic()
                export["elapsed_seconds"] = export["finished"] - export["entered"]
                receipt.setdefault("failure_phase", "export")
                try:
                    signal.signal(signal.SIGALRM, interrupted)
                    arm_deadline(export["deadline"])
                    publish(failure_receipt())
                except BaseException:
                    # A successful initial FAIL remains if replacement never commits.
                    # False means persistence was unavailable, never a claimed receipt.
                    if not initial_fail_published:
                        export_incomplete = True
            # Successful replacement is terminal; never reclassify it or depend
            # on a rescue write. Its alarm was already retired before commit.
        finally:
            if not final_result_committed:
                signal.setitimer(signal.ITIMER_REAL, 0)
    return 0 if receipt["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
