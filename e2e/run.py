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
from typing import Any, Callable, Iterator

from owned import Owned, identity
import completed


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


def scenario_fields(result: dict, sanitize: Callable[[str], str], *, context: list[str],
                    ledger: _EvidenceLedger | None = None,
                    check: Callable[[], None] = lambda: None) -> dict:
    """Privately detach the seven claims using the collection's actual owner.

    Borrowed objects still owe original PLUS safe bytes, including aliases.
    Parsing does not grant a zero-cost original projection obligation.
    """
    if type(result) is not dict or type(result.get("variants")) is not dict:
        raise ValueError("scenario state unavailable")
    if ledger is None:
        ledger = _EvidenceLedger()  # The existing borrowed-object opportunity.
    ledger.charge(8, 1, check)
    ancestors = set()

    def detach(value: object, depth: int = 0) -> Any:
        if depth > 64:
            raise _EvidenceCapacity("scenario projection capacity refused")
        ledger.charge(0, 1, check)
        if type(value) is str:
            size = _evidence_utf8_size(value, check)
            ledger.charge(size, 0, check)
            if "\\" in value:
                raise ValueError("scenario representation withheld")
            return _evidence_safe(value, size, sanitize, context, ledger, check)
        if type(value) in (dict, list):
            if id(value) in ancestors:
                raise ValueError("scenario cycle refused")
            ledger.charge(8, 1, check)  # Active ancestor and private container.
            ancestors.add(id(value))
            try:
                if type(value) is dict:
                    output = {}
                    for key, item in value.items():
                        if type(key) is not str:
                            raise ValueError("scenario key unavailable")
                        safe_key = detach(key, depth + 1)
                        if safe_key in output:
                            raise ValueError("scenario redacted key collision")
                        ledger.charge(16, 1, check)
                        output[safe_key] = detach(item, depth + 1)
                    return output
                output = []
                for item in value:
                    ledger.charge(8, 1, check)
                    output.append(detach(item, depth + 1))
                return output
            finally:
                ancestors.remove(id(value))
        if (value is None or type(value) is bool or type(value) is int and value.bit_length() <= 64
                or type(value) is float and math.isfinite(value)):
            return value
        raise ValueError("scenario value unavailable")

    ledger.charge(8, 1, check)
    detached = {}
    for key in ("variants", "scenario_phases", "e07", "groups", "e07_admission", "failures", "finalization"):
        if key in result:
            safe_key = detach(key, 1)
            if safe_key in detached:
                raise ValueError("scenario redacted key collision")
            ledger.charge(16, 1, check)
            detached[safe_key] = detach(result[key], 1)
    if type(detached.get("variants")) is not dict:
        raise _EvidenceUnsafe("scenario required-key sanitation postcondition")
    # Independent encoded predicate, SAME collection owner, no second buffer.
    _evidence_encoded_size(detached, ledger, check)
    check()
    return detached


class _EvidenceCapacity(ValueError):
    """An ordinary reached work/storage guard, never an input-size witness."""


class _EvidenceUnsafe(ValueError):
    """A broken sanitation postcondition stops the original opportunity."""


class _EvidenceLedger:
    """Nonrefundable logical work shared by evidence preparation and writers.

    This bounds charged transformations, not Python object headers or syscalls.
    """
    def __init__(self) -> None:
        self.work = self.reserved = self.facts = 0

    def reserve(self, size: int, facts: int, check: Callable[[], None]) -> None:
        check()
        if (type(size) is not int or type(facts) is not int or min(size, facts) < 0
                or self.work + self.reserved + size > 8388608 or self.facts + facts > 200000):
            raise _EvidenceCapacity("evidence capacity refused")
        self.reserved += size
        self.facts += facts

    def charge(self, size: int, facts: int, check: Callable[[], None]) -> None:
        self.reserve(size, facts, check)
        self.settle(size, size)

    def settle(self, reserved: int, actual: int) -> None:
        if not 0 <= actual <= reserved <= self.reserved:
            raise ValueError("invalid evidence reservation")
        self.reserved -= reserved
        self.work += actual  # Unused capacity releases; actual work never refunds.


def _evidence_utf8_size(text: str, check: Callable[[], None], limit: int = 8388608) -> int:
    if type(text) is not str:
        raise ValueError("evidence string unavailable")
    size = 0
    for index, char in enumerate(text):
        if index % 4096 == 0:
            check()
        code = ord(char)
        if 0xD800 <= code <= 0xDFFF:
            raise ValueError("evidence surrogate refused")
        size += 1 if code < 128 else 2 if code < 2048 else 3 if code < 65536 else 4
        if size > limit:
            raise _EvidenceCapacity("evidence capacity refused")
    check()
    return size


def _evidence_safe(text: str, size: int, sanitize: Callable[[str], str], context: list[str],
                   ledger: _EvidenceLedger, check: Callable[[], None]) -> str:
    # Original scalar is already charged by the real caller. Plan EVERY pass,
    # including no-match aliases, before the known literal sanitizer allocates.
    spans, expected, peak, changed = _evidence_literal_plan(text, size, context, ledger, check)
    scratch = peak if changed else 0
    ledger.reserve(scratch, 1, check)
    try:
        safe = sanitize(text)
        try:
            actual = _evidence_utf8_size(safe, check, expected)
        except ValueError as exc:
            raise _EvidenceUnsafe("unsafe evidence sanitation postcondition") from exc
        ledger.charge(24, 3, check)
        if (actual != expected or not _evidence_span_equal([(safe, 0, len(safe))], spans, check)
                or any(secret in safe for secret in context)):
            raise _EvidenceUnsafe("unsafe evidence sanitation postcondition")
        check()
    finally:
        # All actual pass work was spent before the call; scratch release
        # cannot erase it, including when sanitation raises.
        ledger.settle(scratch, 0)
    return safe


def _evidence_chars(spans: list[tuple[str, int, int]], check: Callable[[], None]) -> Iterator[str]:
    count = 0
    for text, begin, end in spans:
        for index in range(begin, end):
            if count % 4096 == 0:
                check()
            count += 1
            yield text[index]
    check()


def _evidence_span_equal(left: list, right: list, check: Callable[[], None]) -> bool:
    a, b = _evidence_chars(left, check), _evidence_chars(right, check)
    while True:
        x, y = next(a, None), next(b, None)
        if x != y:
            return False
        if x is None:
            return True


def _evidence_matches(spans: list, secret: str, ledger: _EvidenceLedger,
                      check: Callable[[], None], *, first: bool = False) -> list[int]:
    """Nonoverlapping indexed matches; no whole token or matching carry copy."""
    matches = []
    if len(spans) == 1:
        text, begin, end = spans[0]
        position = begin
        while True:
            check()
            position = text.find(secret, position, end)
            if position < 0:
                return matches
            ledger.charge(16, 2, check)
            matches.append(position - begin)
            if first:
                return matches
            position += len(secret)
    # Only the current original secret has a matcher table, never a dictionary
    # of transformed secrets. Reserve its slots/facts BEFORE allocation.
    chunks = (len(secret) + 511) // 512
    ledger.charge(8 * len(secret) + 8 * chunks, len(secret) + chunks, check)
    prefix = []  # F also bounds the outer reference array to <4096 logical B.
    for begin in range(0, len(secret), 512):
        check()
        prefix.append([0] * min(512, len(secret) - begin))
    matched = 0
    for index in range(1, len(secret)):
        if index % 4096 == 0:
            check()
        while matched and secret[index] != secret[matched]:
            matched = prefix[(matched - 1) // 512][(matched - 1) % 512]
        if secret[index] == secret[matched]:
            matched += 1
        prefix[index // 512][index % 512] = matched
    matched = 0
    for position, char in enumerate(_evidence_chars(spans, check)):
        while matched and char != secret[matched]:
            matched = prefix[(matched - 1) // 512][(matched - 1) % 512]
        if char == secret[matched]:
            matched += 1
        if matched == len(secret):
            ledger.charge(16, 2, check)
            matches.append(position + 1 - matched)
            if first:
                return matches
            matched = 0
    return matches


def _evidence_literal_plan(text: str, size: int, context: list[str] | tuple[str, ...],
                           ledger: _EvidenceLedger, check: Callable[[], None]) -> tuple:
    """Literal replacement as charged ORIGINAL spans, without a safe scalar copy.

    The caller owns the original debit. Retained spans refer to that SAME
    original or the fixed marker. No second decode/sanitation recovers content.
    """
    ledger.charge(24, 3, check)
    spans = [(text, 0, len(text))]
    peak, changed = size, False
    for secret in context:
        if type(secret) is not str or not secret:
            raise _EvidenceUnsafe("evidence context unavailable")
        matches = _evidence_matches(spans, secret, ledger, check)
        secret_size = _evidence_utf8_size(secret, check)
        safe_size = size + len(matches) * (10 - secret_size)
        # EVERY replacement pass pays its complete safe scalar, even aliasing.
        ledger.charge(safe_size, 1, check)
        if matches:
            changed = True
            output = []
            def retain(source: str, begin: int, end: int) -> None:
                if begin < end:
                    ledger.charge(24, 3, check)
                    output.append((source, begin, end))
            match_index, offset, cursor = 0, 0, 0
            for source, begin, end in spans:
                length = end - begin
                while match_index < len(matches) and matches[match_index] < offset + length:
                    start = matches[match_index]
                    retain(source, begin + max(cursor - offset, 0), begin + max(start - offset, 0))
                    retain("[REDACTED]", 0, 10)
                    cursor = start + len(secret)
                    match_index += 1
                retain(source, begin + max(cursor - offset, 0), end)
                offset += length
                cursor = max(cursor, offset)
            spans = output
        size = safe_size
        peak = max(peak, size)
    # Check the complete virtual result, including matches across span joins.
    for secret in context:
        if _evidence_matches(spans, secret, ledger, check, first=True):
            raise _EvidenceUnsafe("unsafe evidence sanitation postcondition")
    check()
    return spans, size, peak, changed


def _evidence_string_size(spans: list, check: Callable[[], None]) -> int:
    size = 2
    for char in _evidence_chars(spans, check):
        code = ord(char)
        if 0xD800 <= code <= 0xDFFF:
            raise ValueError("evidence surrogate refused")
        size += (2 if char in '\"\\\b\f\n\r\t' else 6 if code < 32 else
                 1 if code < 128 else 2 if code < 2048 else 3 if code < 65536 else 4)
        if size > 8388608:
            raise _EvidenceCapacity("evidence encoded capacity refused")
    return size


def _evidence_number(value: int | float, ledger: _EvidenceLedger, check: Callable[[], None]) -> str:
    if not (type(value) is int and value.bit_length() <= 64
            or type(value) is float and math.isfinite(value)):
        raise ValueError("evidence number refused")
    ledger.reserve(128, 1, check)
    try:
        text = repr(value)  # Only a checked builtin 64-bit int or finite float.
        if len(text) > 128:
            raise _EvidenceCapacity("evidence number capacity refused")
    except BaseException:
        ledger.settle(128, 128)
        raise
    ledger.settle(128, len(text))
    return text


def _evidence_encoded_size(value: object, ledger: _EvidenceLedger, check: Callable[[], None]) -> int:
    """Independent compact predicate only; no complete encoded image."""
    size, ancestors = 0, set()
    def add(count: int) -> None:
        nonlocal size
        if size + count > 8388608:
            raise _EvidenceCapacity("evidence encoded capacity refused")
        size += count
    def visit(item: object, depth: int) -> None:
        if depth > 64:
            raise _EvidenceCapacity("evidence depth capacity refused")
        ledger.charge(0, 1, check)
        if type(item) is str:
            ledger.charge(24, 3, check)
            add(_evidence_string_size([(item, 0, len(item))], check))
        elif type(item) in (dict, list):
            if id(item) in ancestors:
                raise ValueError("evidence cycle refused")
            ledger.charge(8, 1, check)
            ancestors.add(id(item))
            try:
                add(2)
                for index, member in enumerate(item.items() if type(item) is dict else item):
                    if index:
                        add(1)
                    if type(item) is dict:
                        key, child = member
                        if type(key) is not str:
                            raise ValueError("evidence key unavailable")
                        visit(key, depth + 1)
                        add(1)
                        visit(child, depth + 1)
                    else:
                        visit(member, depth + 1)
            finally:
                ancestors.remove(id(item))
        elif item is None or type(item) is bool:
            add(4 if item is None or item is True else 5)
        elif type(item) in (int, float):
            add(len(_evidence_number(item, ledger, check)))
        else:
            raise ValueError("evidence value unavailable")
    visit(value, 0)
    check()
    return size


def _evidence_buffer(tokens: list, size: int, ledger: _EvidenceLedger,
                      check: Callable[[], None]) -> bytearray:
    """One complete candidate, exact reservation; emission operations <=6B."""
    ledger.charge(size, 1, check)
    ledger.charge(8, 2, check)  # Fixed codepoint/escape scratch, before allocation.
    candidate = bytearray(size)
    position, checkpoint = 0, 0
    def put(code: int) -> None:
        nonlocal position, checkpoint
        if position >= size:
            raise _EvidenceUnsafe("evidence encoding postcondition")
        if position >= checkpoint:
            check()
            checkpoint = position + 4096
        candidate[position] = code
        position += 1
    for kind, content in tokens:
        if kind == "string":
            put(34)
        for char in _evidence_chars(content, check):
            code = ord(char)
            if kind == "string" and (code < 32 or char in '\"\\'):
                put(92)
                escape = '\"\\\b\f\n\r\t'.find(char)
                if escape < 0:
                    for digit in (117, 48, 48, ord("0123456789abcdef"[code >> 4]), ord("0123456789abcdef"[code & 15])):
                        put(digit)
                else:
                    put((34, 92, 98, 102, 110, 114, 116)[escape])
            elif code < 128:
                put(code)
            elif code < 2048:
                put(192 | code >> 6)
                put(128 | code & 63)
            elif code < 65536:
                put(224 | code >> 12)
                put(128 | code >> 6 & 63)
                put(128 | code & 63)
            else:
                put(240 | code >> 18)
                put(128 | code >> 12 & 63)
                put(128 | code >> 6 & 63)
                put(128 | code & 63)
        if kind == "string":
            put(34)
    check()
    if position != size:
        raise _EvidenceUnsafe("evidence encoding postcondition")
    return candidate


def _evidence_original(text: str, context: tuple | list, ledger: _EvidenceLedger,
                       check: Callable[[], None], sanitize: Callable[[str], str]) -> None:
    """Unchanged-safe original JSON spelling, never a decoded-text grant."""
    try:
        size = _evidence_utf8_size(text, check)
        ledger.charge(size, 1, check)
        for secret in context:
            ledger.charge(8, 1, check)
            if secret in text:
                raise ValueError("original evidence content withheld")
        if _evidence_safe(text, size, sanitize, context, ledger, check) != text:
            raise _EvidenceUnsafe("original unchanged postcondition")
    finally:
        text = None


def _evidence_digest(text: str, ledger: _EvidenceLedger, check: Callable[[], None]) -> tuple[int, str]:
    """Hash the entire original UTF8 image in precharged <=4096-byte chunks."""
    size = _evidence_utf8_size(text, check)
    ledger.charge(size + 96, 12, check)
    digest = hashlib.sha256()
    # At most1024 codepoints, hence at most4096 encoded bytes. Both the slice
    # and its conversion are reserved before either is allocated.
    for index in range(0, len(text), 1024):
        check()
        end = min(index + 1024, len(text))
        ledger.charge(8 * (end - index), 2, check)
        chunk = text[index:end].encode('utf8', errors='strict')
        digest.update(chunk)
        chunk = None
    ledger.charge(64, 1, check)
    return size, digest.hexdigest()


def _evidence_submit_digest(payload: dict, ledger: _EvidenceLedger, check: Callable[[], None]) -> str:
    """Actual HTTPX compact UTF8 request, preserving producer insertion order."""
    ledger.charge(128, 16, check)
    digest = hashlib.sha256()
    def put(text: str, string: bool = False) -> None:
        ledger.charge(24, 3, check)
        size = _evidence_string_size([(text, 0, len(text))], check) if string else len(text)
        ledger.charge(3 * size, 1, check)
        ledger.reserve(8, 1, check)
        try:
            if string:
                digest.update(b'"')
            for index, char in enumerate(text):
                if index % 1024 == 0:
                    check()
                code = ord(char)
                if string and (code < 32 or char in '"\\'):
                    pos = '"\\\b\f\n\r\t'.find(char)
                    if pos >= 0:
                        data = ('\\' + '"\\bfnrt'[pos]).encode('ascii')
                    else:
                        data = ('\\u00' + '0123456789abcdef'[code >> 4] + '0123456789abcdef'[code & 15]).encode('ascii')
                else:
                    data = char.encode('utf8', errors='strict')
                digest.update(data)
            if string:
                digest.update(b'"')
        finally:
            ledger.settle(8, 0)
    def visit(value: object, depth: int = 0) -> None:
        # Only closed submit/completion facts reach this request digest. Keep
        # the actual CLI insertion order, including done and waiting arrays.
        if depth > 64:
            raise _EvidenceCapacity('request depth capacity refused')
        ledger.charge(16, 2, check)
        if type(value) is dict:
            put('{')
            for index, (key, child) in enumerate(value.items()):
                if index:
                    put(',')
                put(key, True); put(':'); visit(child, depth + 1)
            put('}')
        elif type(value) is list:
            put('[')
            for index, child in enumerate(value):
                if index:
                    put(',')
                visit(child, depth + 1)
            put(']')
        elif type(value) is str:
            put(value, True)
        else:
            put('null' if value is None else 'true' if value is True else 'false' if value is False
                else _evidence_number(value, ledger, check))
    visit(payload)
    ledger.charge(64, 1, check)
    return digest.hexdigest()


def _evidence_python(source: str, context: tuple | list, ledger: _EvidenceLedger,
                     check: Callable[[], None], sanitize: Callable[[str], str], depth: int) -> None:
    """Finite SOURCE lexical admission only; no Python parser or execution.

    Preflight uses indices, never physical-line/token copies. Each selected
    occurrence owns its scan and original/decoded-literal unchanged checks.
    """
    try:
        if depth + 2 > 64:
            raise _EvidenceCapacity('Python depth capacity refused')
        size = _evidence_utf8_size(source, check, 524288)
        ledger.charge(size + 64, 8, check)
        line = 0
        for index, char in enumerate(source):
            if index % 4096 == 0:
                check()
            code = ord(char)
            line += 1 if code < 128 else 2 if code < 2048 else 3 if code < 65536 else 4
            if line > 65536:
                raise _EvidenceCapacity('Python physical line capacity refused')
            if char == '\r' and (index + 1 == len(source) or source[index + 1] != '\n'):
                raise ValueError('Python lone CR withheld')
            if char == '\n':
                line = 0
        _evidence_original(source, context, ledger, check, sanitize)
        end, index, stack = len(source), 0, []

        def fail() -> None:
            raise ValueError('Python lexical representation withheld')

        def literal(begin: int, quote_at: int) -> int:
            ledger.charge(64, 8, check)
            prefix = ''.join(source[n].lower() for n in range(begin, quote_at))
            if prefix not in ('', 'u', 'b', 'r', 'br', 'rb'):
                fail()
            raw, binary = 'r' in prefix, 'b' in prefix
            quote = source[quote_at]
            triple = source.startswith(quote * 3, quote_at)
            opening = quote_at + (3 if triple else 1)

            def codes() -> Iterator[int]:
                cursor = opening
                while cursor < end:
                    if cursor % 1024 == 0:
                        check()
                    char = source[cursor]
                    if char == quote and (not triple or source.startswith(quote * 3, cursor)):
                        return
                    if char in '\r\n' and not triple:
                        fail()
                    code = ord(char)
                    cursor += 1
                    if char == '\r' and triple:
                        if cursor == end or source[cursor] != '\n':
                            fail()
                        cursor += 1
                        code = 10
                    if binary and code >= 128:
                        fail()
                    if char == '\\':
                        if cursor == end:
                            fail()
                        char = source[cursor]
                        cursor += 1
                        if raw:
                            # Backslash protects a quote for termination, but remains
                            # literal content and therefore owes residual safety.
                            yield 92
                            if char == '\r':
                                if cursor == end or source[cursor] != '\n':
                                    fail()
                                cursor += 1
                                yield 13
                                yield 10
                            else:
                                yield ord(char)
                            continue
                        if char == '\n':
                            continue
                        if char == '\r':
                            if cursor == end or source[cursor] != '\n':
                                fail()
                            cursor += 1
                            continue
                        escapes = "\\\"'abfnrtv"
                        if char in escapes:
                            code = (92, 34, 39, 7, 8, 12, 10, 13, 9, 11)[escapes.index(char)]
                        elif char in '01234567':
                            code, count = int(char), 1
                            while count < 3 and cursor < end and source[cursor] in '01234567':
                                code = code * 8 + int(source[cursor]); cursor += 1; count += 1
                            if binary and code > 255:
                                fail()
                        elif char in 'xuU' and (char == 'x' or not binary):
                            digits = 2 if char == 'x' else 4 if char == 'u' else 8
                            code = 0
                            for _ in range(digits):
                                if cursor == end or source[cursor] not in '0123456789abcdefABCDEF':
                                    fail()
                                code = code * 16 + int(source[cursor], 16); cursor += 1
                        else:
                            fail()
                    if code > 0x10ffff or 0xd800 <= code <= 0xdfff:
                        fail()
                    yield code
                fail()

            def decoded() -> Iterator[int]:
                needed = value = minimum = 0
                for code in codes():
                    if not binary:
                        yield code
                    elif needed:
                        if not 128 <= code <= 191:
                            fail()
                        value = (value << 6) | (code & 63)
                        needed -= 1
                        if not needed:
                            if value < minimum or value > 0x10ffff or 0xd800 <= value <= 0xdfff:
                                fail()
                            yield value
                    elif code < 128:
                        yield code
                    elif 194 <= code <= 244:
                        needed, value, minimum = ((1, code & 31, 128) if code < 224 else
                                                  (2, code & 15, 2048) if code < 240 else
                                                  (3, code & 7, 65536))
                    else:
                        fail()
                if needed:
                    fail()

            # Locate the closing spelling without slices, including escaped quote
            # and raw termination rules. Grammar/decoded length preflight follows.
            cursor = opening
            while cursor < end:
                if cursor % 1024 == 0:
                    check()
                if source[cursor] == quote and (not triple or source.startswith(quote * 3, cursor)):
                    finish = cursor + (3 if triple else 1)
                    break
                if source[cursor] == '\\':
                    cursor += 1
                    if cursor == end:
                        fail()
                    if source[cursor] == '\r' and cursor + 1 < end and source[cursor + 1] == '\n':
                        cursor += 1
                cursor += 1
            else:
                fail()
            spelling_size = 0
            for n in range(begin, finish):
                if n % 1024 == 0:
                    check()
                code = ord(source[n])
                spelling_size += 1 if code < 128 else 2 if code < 2048 else 3 if code < 65536 else 4
                if spelling_size > 262144:
                    raise _EvidenceCapacity('Python literal spelling capacity refused')
            decoded_size = 0
            for code in decoded():
                decoded_size += 1 if code < 128 else 2 if code < 2048 else 3 if code < 65536 else 4
                if decoded_size > 262144:
                    raise _EvidenceCapacity('Python literal output capacity refused')
            chunks = (decoded_size + 1020) // 1021
            ledger.charge(2 * decoded_size + 8 * chunks + 32, chunks + 4, check)
            ledger.reserve(min(4096, 4 * decoded_size), 1, check)
            try:
                parts, chunk, width = [], '', 0
                for code in decoded():
                    count = 1 if code < 128 else 2 if code < 2048 else 3 if code < 65536 else 4
                    if width + count > 1024:
                        parts.append(chunk); chunk, width = '', 0
                        check()
                    ledger.charge(width + 2 * count, 0, check)
                    chunk += chr(code); width += count
                if chunk:
                    parts.append(chunk)
                value = ''.join(parts)
                if '\\' in value:
                    fail()
                _evidence_original(value, context, ledger, check, sanitize)
            finally:
                ledger.settle(min(4096, 4 * decoded_size), 0)
                parts = chunk = value = None
            return finish

        while index < end:
            if index % 1024 == 0:
                check()
            char, begin = source[index], index
            if char in ' \t\f\r\n':
                index += 1
                continue
            ledger.charge(16, 3, check)  # Token/span, before retaining any state.
            if char == '#':
                while index < end and source[index] not in '\r\n':
                    if index % 4096 == 0:
                        check()
                    if source[index] == '\\':
                        fail()
                    index += 1
                continue
            if char in "\"'":
                index = literal(index, index)
                continue
            if char.isascii() and (char.isalpha() or char == '_'):
                index += 1
                while index < end and source[index].isascii() and (source[index].isalnum() or source[index] == '_'):
                    if index - begin >= 65536:
                        raise _EvidenceCapacity('Python token capacity refused')
                    if index % 4096 == 0:
                        check()
                    index += 1
                if index < end and source[index] in "\"'":
                    if index - begin > 2:
                        fail()
                    index = literal(begin, index)
                continue
            if char in '0123456789' or char == '.' and index + 1 < end and source[index + 1] in '0123456789':
                def digits(cursor: int) -> int:
                    count, underscore = 0, False
                    while cursor < end and source[cursor] in '0123456789_':
                        if cursor - begin >= 65536:
                            raise _EvidenceCapacity('Python token capacity refused')
                        if cursor % 4096 == 0:
                            check()
                        if source[cursor] == '_':
                            if not count or underscore:
                                fail()
                            underscore = True
                        else:
                            count += 1; underscore = False
                        cursor += 1
                    if underscore:
                        fail()
                    return cursor
                index = digits(index)
                if index < end and source[index] == '.':
                    index = digits(index + 1)
                if index < end and source[index] in 'eE':
                    index += 1
                    if index < end and source[index] in '+-':
                        index += 1
                    start = index; index = digits(index)
                    if start == index:
                        fail()
                if index < end and (source[index].isascii() and (source[index].isalpha() or source[index] == '_')):
                    fail()
                continue
            if char in '([{':
                if depth + len(stack) + 3 > 64:
                    raise _EvidenceCapacity('Python depth capacity refused')
                ledger.charge(8, 1, check)
                stack.append(char)
            elif char in ')]}':
                if not stack or '([{'.index(stack.pop()) != ')]}'.index(char):
                    fail()
            elif char == '\\':
                index += 1
                if index < end and source[index] == '\r':
                    index += 1
                if index == end or source[index] != '\n':
                    fail()
            elif char not in ':.,;+-*/%&|^~<>=!@':
                fail()
            elif char == '!' and (index + 1 == end or source[index + 1] != '='):
                fail()
            index += 1
        if stack:
            fail()
        check()
    finally:
        source = None


def _evidence_json(value: object, context: tuple[str, ...], ledger: _EvidenceLedger,
                    check: Callable[[], None], *, observations: bool = False,
                    submission_identity: tuple | None = None) -> bytearray:
    """Typed original admission -> flat charged spans -> ONE compact candidate.

    No second writer tree. Original references and syntax slots close metadata
    first; no sanitation/emission chooses a policy before a later identity key.
    """
    tokens, ancestors = [], set()
    authenticated = changed = False
    def retain(kind: str, content: object) -> None:
        ledger.charge(24, 3, check)
        tokens.append((kind, content))
    def sanitize(text: str) -> str:
        for secret in context:
            text = text.replace(secret, "[REDACTED]")
        return text
    def visit(item: object, depth: int = 0, *, key: bool = False, route: int = 0,
              event: Callable | None = None, encoded: bool = False,
              selected: Callable | None = None, selected_path: tuple = ()) -> None:
        nonlocal authenticated, changed
        if depth > 64:
            raise _EvidenceCapacity("evidence depth capacity refused")
        ledger.charge(0, 1, check)
        if selected is not None:
            ledger.charge(8 * len(selected_path), len(selected_path), check)
            selected(selected_path, 'object' if type(item) is dict else 'array' if type(item) is list else 'scalar', item if type(item) not in (dict, list) else None)
            encoded = encoded or _EvidenceObservations.encoded(selected_path)
        if route == 2 and type(item) is not list or route == 3 and type(item) is not dict:
            raise ValueError("Relay entry shape withheld")
        if type(item) is str:
            size = _evidence_utf8_size(item, check)
            ledger.charge(size, 0, check)
            if "\\" in item and not encoded:
                raise ValueError("evidence representation withheld")
            if event is not None:
                event("key" if key else "scalar", item)
            changed = changed or any(secret in item for secret in context)
            if key:
                authenticated = authenticated or item in ("bytes", "byte_length", "sha256", "original_byte_check", "entity_copy") or item.endswith(("_bytes", "_sha256"))
            retain("key" if key else "original", (item, size))
        elif type(item) in (dict, list):
            if id(item) in ancestors:
                raise ValueError("evidence cycle refused")
            ledger.charge(8, 1, check)
            ancestors.add(id(item))
            proof = _EvidenceRelay(context, ledger, check, sanitize, depth) if route == 3 else None
            selection = None
            try:
                if event is not None:
                    event("object" if type(item) is dict else "array", None)
                retain("syntax", "{" if type(item) is dict else "[")
                for index, member in enumerate(item.items() if type(item) is dict else item):
                    if index:
                        retain("syntax", ",")
                    if type(item) is dict:
                        name, child = member
                        if type(name) is not str:
                            raise ValueError("evidence key unavailable")
                        visit(name, depth + 1, key=True)
                        if selected is not None:
                            selected(selected_path, 'key', name)
                        if route == 1 and name in ('starts', 'durable', 'provider_commands', 'provider_attempts') and selection is None:
                            selection = _EvidenceObservations(context, ledger, check, sanitize, depth, submission_identity)
                        if event is not None:
                            event("key", name)
                        retain("syntax", ":")
                        if proof is not None:
                            proof.field(name, child)
                        visit(child, depth + 1, route=2 if route == 1 and name == "relay" else 0,
                              event=(proof.response_event if name == "response" else proof.request_event
                                     if name == "request" else None) if proof is not None else event,
                              encoded=proof is not None and name == "response_text",
                              selected=selection.event if route == 1 and name in ('starts', 'durable', 'provider_commands', 'provider_attempts') else selected,
                              selected_path=(name,) if route == 1 else selected_path + (name,) if selected is not None else ())
                    else:
                        visit(member, depth + 1, route=3 if route == 2 else 0, event=event,
                              selected=selected, selected_path=selected_path + (index,) if selected is not None else ())
                retain("syntax", "}" if type(item) is dict else "]")
                if event is not None:
                    event("end-object" if type(item) is dict else "end-array", None)
                if selected is not None:
                    selected(selected_path, 'end-object' if type(item) is dict else 'end-array', None)
                if selection is not None:
                    capture = selection.close()
                    authenticated = authenticated or capture
                if proof is not None:
                    capture = proof.close()
                    authenticated = authenticated or capture
            finally:
                if selection is not None:
                    selection.release()
                if proof is not None:
                    proof.release()
                ancestors.remove(id(item))
        elif item is None or type(item) is bool:
            if event is not None:
                event("scalar", item)
            retain("syntax", "null" if item is None else "true" if item else "false")
        elif type(item) in (int, float):
            if event is not None:
                event("scalar", item)
            retain("syntax", _evidence_number(item, ledger, check))
        else:
            raise ValueError("evidence value unavailable")
    visit(value, route=1 if observations else 0)
    if authenticated and changed:
        raise ValueError("authenticated evidence requires original bytes")
    size, keys = 0, []
    for index, (kind, content) in enumerate(tokens):
        check()
        if kind == "syntax":
            if content == "{":
                ledger.charge(8, 1, check)
                keys.append([])
            elif content == "}":
                keys.pop()
            ledger.charge(24, 3, check)
            spans = [(content, 0, len(content))]
            tokens[index] = ("literal", spans)
            count = len(content)
        else:
            text, original_size = content
            if not context:
                # Closed result fields are already selected safe values. Even
                # an unchanged alias owes its independent safe-string debit.
                ledger.charge(original_size, 1, check)
            spans, safe_size, _, _ = _evidence_literal_plan(text, original_size, context, ledger, check)
            if kind == "key":
                for previous, previous_size in keys[-1]:
                    if safe_size == previous_size:
                        ledger.charge(safe_size + previous_size, 1, check)
                        if _evidence_span_equal(spans, previous, check):
                            raise ValueError("evidence redacted key collision")
                ledger.charge(safe_size + 16, 1, check)
                keys[-1].append((spans, safe_size))
            tokens[index] = ("string", spans)
            count = _evidence_string_size(spans, check)
        if size + count > 8388608:
            raise _EvidenceCapacity("evidence encoded capacity refused")
        size += count
    check()
    candidate = _evidence_buffer(tokens, size, ledger, check)
    # Scalar sanitation cannot see a secret spanning the encoded punctuation.
    # Match EVERY detailed candidate, without a whole encoded-secret temporary.
    for secret in context:
        count = _evidence_utf8_size(secret, check)
        ledger.charge(count + len(candidate) + 64, 8, check)
        stop = min(1024, len(secret))
        ledger.charge(8 * stop, 2, check)  # Slice and <=4096-byte conversion.
        prefix = secret[:stop].encode("utf8")
        position = candidate.find(prefix)
        while position >= 0:
            check()
            cursor = position + len(prefix)
            for begin in range(stop, len(secret), 1024):
                end = min(begin + 1024, len(secret))
                ledger.charge(8 * (end - begin), 2, check)
                chunk = secret[begin:end].encode("utf8")
                ledger.charge(len(chunk), 1, check)
                if not candidate.startswith(chunk, cursor):
                    break
                cursor += len(chunk)
            else:
                raise ValueError("original literal content withheld")
            # Each additional prefix search spends work; no failed work refund.
            ledger.charge(len(candidate) - position, 1, check)
            position = candidate.find(prefix, position + 1)
    check()
    return candidate


def _evidence_input(stream: Any, ledger: _EvidenceLedger, check: Callable[[], None],
                    returned: Callable[[Any], None], measured: Callable[[int], None],
                    *, binary: bool = False) -> str | bytes:
    """The original single read; metadata never supplies a content image."""
    check()
    before = os.fstat(stream.fileno())
    named = os.stat(stream.name, follow_symlinks=False)
    fields = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
    signature = tuple(getattr(before, key) for key in fields)
    if not stat.S_ISREG(before.st_mode) or tuple(getattr(named, k) for k in fields) != signature:
        raise ValueError("evidence input identity refused")
    bound = min(8388608, before.st_size)
    if binary:
        ledger.charge(1, 1, check)  # Rejection-only byte sentinel, before read.
    ledger.reserve(bound, 0, check)  # Refuse BEFORE read if remaining space is smaller.
    try:
        text = stream.read(bound + 1)  # Rejection-only sentinel, never parse capacity.
        returned(text)  # Original returned-slice annotation precedes later checks.
        check()
        size = len(text) if binary else _evidence_utf8_size(text, check, 33554436)
        measured(size)
        check()
        after = os.fstat(stream.fileno())
        named = os.stat(stream.name, follow_symlinks=False)
        if (tuple(getattr(after, k) for k in fields) != signature
                or tuple(getattr(named, k) for k in fields) != signature):
            raise ValueError("evidence input changed")
        if size > bound or len(text) > bound:
            raise _EvidenceCapacity("evidence input capacity refused")
        if size != before.st_size:
            raise ValueError("short evidence input refused")
    except BaseException:
        # A failed read/returned prefix cannot refund its reserved work.
        ledger.settle(bound, bound)
        if binary:
            text = None  # Never retain a refused original image in this frame.
        raise
    ledger.settle(bound, size)
    return text


class _EvidenceRelay:
    """Only observations.relay[*]: one capture record and its response edge.

    The response tape is indispensable ordered equality state, not a raw tree
    or a general token index. Request content streams into its original HTTPX
    compact-JSON digest. Nothing outside this record is retained.
    """
    fields = ("path", "method", "body_sha256", "status", "response", "dropped",
              "request", "response_text", "entered")

    def __init__(self, context: tuple | list, ledger: _EvidenceLedger,
                 check: Callable[[], None], sanitize: Callable[[str], str], depth: int) -> None:
        ledger.charge(96, 12, check)
        self.context, self.ledger, self.check, self.sanitize, self.depth = context, ledger, check, sanitize, depth
        self.metadata, self.response, self.request_stack = {}, [], []
        self.request_hash = hashlib.sha256()
        self.request_top = None

    def field(self, name: str, value: object) -> None:
        self.ledger.charge(16 + _evidence_utf8_size(name, self.check), 2, self.check)
        # response/request are represented by their stream, never another tree.
        if name in self.fields and name not in ("request", "response"):
            if type(value) not in (str, int, float, bool, type(None)):
                raise ValueError("Relay metadata shape withheld")
            size = _evidence_utf8_size(value, self.check) if type(value) is str else 8
            self.ledger.charge(size, 1, self.check)
            self.metadata[name] = value
        else:
            self.metadata[name] = None

    def response_event(self, kind: str, value: object) -> None:
        size = _evidence_utf8_size(value, self.check) if type(value) is str else 8
        self.ledger.charge(24 + size, 4, self.check)
        self.response.append((kind, value))

    def request_event(self, kind: str, value: object) -> None:
        self.ledger.charge(24, 3, self.check)
        if self.request_top is None:
            self.request_top = kind if kind != "scalar" else type(value)
        def put(text: str, string: bool = False) -> None:
            self.ledger.charge(48, 5, self.check)
            spans = [(text, 0, len(text))]
            size = _evidence_string_size(spans, self.check) if string else len(text)
            data = _evidence_buffer([("string" if string else "literal", spans)], size,
                                    self.ledger, self.check)
            self.request_hash.update(data)
        if kind in ("object", "array", "scalar") and self.request_stack and self.request_stack[-1][0] == "array":
            if self.request_stack[-1][1]:
                put(",")
            self.request_stack[-1][1] += 1
        if kind in ("object", "array"):
            self.ledger.charge(16, 2, self.check)
            self.request_stack.append([kind, 0])
            put("{" if kind == "object" else "[")
        elif kind.startswith("end-"):
            self.request_stack.pop()
            put("}" if kind == "end-object" else "]")
        elif kind == "key":
            if self.request_stack[-1][1]:
                put(",")
            self.request_stack[-1][1] += 1
            put(value, True)
            put(":")
        elif type(value) is str:
            put(value, True)
        else:
            put("null" if value is None else "true" if value is True else "false" if value is False
                else _evidence_number(value, self.ledger, self.check))

    def original(self, text: str) -> None:
        _evidence_original(text, self.context, self.ledger, self.check, self.sanitize)

    def scalar(self, text: str) -> None:
        if "\\" in text:
            raise ValueError("Relay decoded representation withheld")
        self.original(text)

    def close(self) -> bool:
        m = self.metadata
        if "response_text" not in m:
            return False  # Ordinary legacy records have no invented nested edge.
        self.ledger.charge(24 * len(m) + 72, 3 * len(m) + 9, self.check)
        if (set(m) != set(self.fields) or type(m["path"]) is not str
                or type(m["method"]) is not str or type(m["body_sha256"]) is not str
                or type(m["status"]) is not int or m["status"] not in (200, 403, 404, 409, 422)
                or m["dropped"] is not False or type(m["response_text"]) is not str
                or type(m["entered"]) not in (int, float) or not math.isfinite(m["entered"]) or m["entered"] < 0
                or not self.response or self.response[0] != ("object", None)):
            raise ValueError("Relay capture identity withheld")
        path, method, digest = m["path"], m["method"], m["body_sha256"]
        pieces = path.count("/") + 1
        self.ledger.charge(2 * len(path) + 32 * pieces + 64, 6 * pieces + 8, self.check)
        parts = path.split("/")
        # These are the actual E05 CLI Relay endpoints, not a URL/suffix grant.
        selected = method == "GET" and path == "/api/v1/orgs"
        thread_detail = None
        if parts[:5] == ["", "api", "v1", "orgs", "alpha"]:
            tail = parts[5:]
            thread = len(tail) >= 2 and tail[0] == "threads" and tail[1].startswith("THR-") and tail[1][4:].isascii() and tail[1][4:].isdigit()
            task = len(tail) >= 2 and tail[0] == "tasks" and tail[1].startswith("TASK-") and tail[1][5:].isascii() and tail[1][5:].isdigit()
            if method == "GET" and thread and len(tail) == 2:
                thread_detail = tail[1]
            selected = selected or (method == "GET" and (tail == ["identities"] or (len(tail) == 2 and (thread or task))))
            selected = selected or (method == "POST" and (tail in (["threads"], ["identities", "resolve"])
                or thread and len(tail) == 3 and tail[2] in ("reply", "decline", "dispatch", "send")
                or task and len(tail) == 3 and tail[2] == "completion"))
        if (not selected or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)
                or method == "GET" and (self.request_top is not type(None)
                    or digest != "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
                or method == "POST" and (self.request_top != "object" or digest != self.request_hash.hexdigest())
                or m["status"] == 200 and thread_detail is not None
                    and (len(self.response) < 3 or self.response[1] != ("key", "thread_id")
                         or self.response[2] != ("scalar", thread_detail))
                or m["status"] != 200 and (len(self.response) < 2 or self.response[1] != ("key", "detail"))):
            raise ValueError("Relay request/status identity withheld")
        text = m["response_text"]
        position = 0
        def compare(kind: str, value: object) -> None:
            nonlocal position
            other = None
            try:
                size = _evidence_utf8_size(value, self.check) if type(value) is str else 8
                self.ledger.charge(24 + 2 * size, 4, self.check)
                if position >= len(self.response):
                    raise ValueError("Relay response equality withheld")
                other_kind, other = self.response[position]
                if (kind != other_kind or type(value) is not type(other) or value != other
                        or type(value) is float and math.copysign(1, value) != math.copysign(1, other)):
                    raise ValueError("Relay response equality withheld")
                position += 1
            finally:
                value = other = None
        try:
            self.original(text)  # Whole original nested image, before its single parse.
            self.ledger.charge(32, 4, self.check)
            _EvidenceJSON(text, self.ledger, self.check).scan("{", self.scalar, depth=self.depth + 1, event=compare)
            if position != len(self.response):
                raise ValueError("Relay response equality withheld")
            return True
        finally:
            text = None

    def release(self) -> None:
        self.metadata.clear()
        self.response.clear()
        self.request_stack.clear()


class _EvidenceSubmission:
    """Finite proof facts for ONE canonical provider command, never a raw tree."""
    fields = ('action', 'operation', 'argv', 'exit', 'stdout', 'stderr', 'payload', 'payload_file',
              'entered', 'finished', 'timed_out', 'execution_deadline', 'provider', 'child_pid',
              'child_start', 'child_parent', 'child_stat', 'python', 'source_sha', 'callback_sha256', 'wire')
    payload_fields = ('title', 'rationale', 'script', 'interpreter', 'cwd_hint', 'review_required',
                      'persistent', 'max_runtime_seconds', 'task_id', 'session_id')
    plan_fields = payload_fields[:-2]
    completion_fields = ('status', 'confidence', 'summary', 'task_id', 'session_id', 'agent',
                         'decision', 'waiting_on_job_ids', 'verdict')
    result_fields = ('id', 'status', 'created_at', 'started_at', 'cwd_resolved', 'timeout_seconds',
                     'events_url', 'authentication')

    def __init__(self, owner: _EvidenceObservations, depth: int) -> None:
        owner.ledger.charge(128, 16, owner.check)
        self.owner, self.depth = owner, depth
        self.metadata, self.payload, self.provider, self.file = {}, {}, {}, {}
        self.response, self.authentication, self.wire, self.argv = {}, {}, {}, []
        self.shapes, self.deferred = {}, False
        self.receipts, self.read_invalid = [], False
        self.decision, self.waits = {}, []

    def event(self, path: tuple, kind: str, value: object) -> None:
        ledger, check = self.owner.ledger, self.owner.check
        ledger.charge(16, 2, check)
        if path and path[0] == 'wire':
            self.read_event(path[1:], kind, value)
        # Only named source-owned holders can retain facts. Unsupported holders
        # record a shape error; they cannot create an arbitrary subtree/index.
        if kind in ('object', 'array'):
            if path in ((), ('provider',), ('payload',), ('payload_file',), ('wire',), ('argv',),
                        ('wire', 0), ('wire', 0, 'response'), ('wire', 0, 'response', 'authentication'),
                        ('payload', 'decision'), ('payload', 'waiting_on_job_ids')):
                ledger.charge(16, 2, check)
                self.shapes[path] = [kind, []]
            else:
                self.deferred = True
            return
        if kind == 'key':
            if path in self.shapes:
                ledger.charge(16 + _evidence_utf8_size(value, check), 2, check)
                self.shapes[path][1].append(value)
            return
        if kind.startswith('end-'):
            return
        if type(value) is str:
            ledger.charge(_evidence_utf8_size(value, check), 1, check)
        if len(path) == 1 and path[0] in self.fields:
            self.metadata[path[0]] = value
        elif len(path) == 2 and path[0] == 'provider' and path[1] in ('task','session','agent','org','pid'):
            self.provider[path[1]] = value
        elif len(path) == 2 and path[0] == 'payload' and path[1] in self.payload_fields + self.completion_fields:
            self.payload[path[1]] = value
        elif len(path) == 3 and path[:2] == ('payload', 'decision') and path[2] in ('action', 'summary'):
            self.decision[path[2]] = value
        elif len(path) == 3 and path[:2] == ('payload', 'waiting_on_job_ids') and path[2] == len(self.waits):
            ledger.charge(8, 1, check)
            self.waits.append(value)
        elif len(path) == 2 and path[0] == 'payload_file' and path[1] in ('path','text','bytes','sha256'):
            self.file[path[1]] = value
        elif len(path) == 2 and path[0] == 'argv' and path[1] == len(self.argv):
            ledger.charge(8, 1, check)
            self.argv.append(value)
        elif path[:2] == ('wire', 0):
            if len(path) == 3 and path[2] in ('path','method','body_sha256','status','dropped'):
                self.wire[path[2]] = value
            elif len(path) == 4 and path[2] == 'response' and path[3] in self.result_fields + ('deferred','code','ok'):
                self.response[path[3]] = value
            elif len(path) == 5 and path[2:4] == ('response', 'authentication') and path[4] in ('task_id','session_id'):
                self.authentication[path[4]] = value
            else:
                self.deferred = True
        else:
            self.deferred = True

    def ordinary(self, text: str) -> None:
        if '\\' in text:
            raise ValueError('submission ordinary representation withheld')
        _evidence_original(text, self.owner.context, self.owner.ledger, self.owner.check, self.owner.sanitize)

    def object(self, text: str, expected: dict, *, script: bool, stdout: bool = False,
               receipt: bool = False, completion: bool = False, ordered: bool = False) -> None:
        """One strict nested parse, complete keyed equality, original ordering."""
        try:
            o = self.owner
            _evidence_original(text, o.context, o.ledger, o.check, o.sanitize)
            if not text.startswith('{') or not text.endswith('}\n' if stdout else '}'):
                raise ValueError('submission original framing withheld')
            o.ledger.charge(128 + 32 * len(expected), 16 + 4 * len(expected), o.check)
            frames, field = [], None
            def compare(kind: str, value: object) -> None:
                nonlocal field
                o.ledger.charge(24, 3, o.check)
                if kind == 'object':
                    if not frames:
                        frames.append((expected, list(expected) if ordered else sorted(expected), 0))
                    elif (len(frames) == 1 and not script and
                          (field == 'authentication' or receipt and field in ('job', 'output')
                           or completion and field == 'decision')
                          and type(expected.get(field)) is dict):
                        child = expected[field]
                        o.ledger.charge(32 * len(child), 4 * len(child), o.check)
                        frames.append((child, sorted(child), 0))
                    else:
                        raise ValueError('submission nested shape withheld')
                elif kind == 'array' and completion and len(frames) == 1 and field == 'waiting_on_job_ids':
                    child = expected.get(field)
                    if type(child) is not list:
                        raise ValueError('completion waiting shape withheld')
                    o.ledger.charge(24, 3, o.check)
                    frames.append((child, None, 0))
                elif kind == 'end-array' and frames and frames[-1][1] is None:
                    wanted, _, position = frames.pop()
                    if position != len(wanted):
                        raise ValueError('completion complete waiting equality withheld')
                    field = None
                elif kind == 'end-object':
                    wanted, keys, position = frames.pop()
                    if position != len(keys):
                        raise ValueError('submission complete equality withheld')
                    if frames:
                        field = None
                elif kind == 'key':
                    self.ordinary(value)
                    wanted, keys, position = frames[-1]
                    if position >= len(keys) or value != keys[position]:
                        raise ValueError('submission sorted fields withheld')
                    frames[-1] = (wanted, keys, position + 1)
                    field = value
                elif kind == 'scalar':
                    wanted, keys, position = frames[-1]
                    if keys is None:
                        if position >= len(wanted) or type(value) is not type(wanted[position]) or value != wanted[position]:
                            raise ValueError('completion ordered waiting equality withheld')
                        if type(value) is str:
                            self.ordinary(value)
                        frames[-1] = (wanted, None, position + 1)
                        return
                    if type(value) is str:
                        o.ledger.charge(_evidence_utf8_size(value, o.check) +
                            (_evidence_utf8_size(wanted.get(field), o.check) if type(wanted.get(field)) is str else 0), 1, o.check)
                    if field not in wanted or type(value) is not type(wanted[field]) or value != wanted[field]:
                        raise ValueError('submission complete equality withheld')
                    if type(value) is str:
                        if script and field == 'script' or receipt and field == 'script_text':
                            _evidence_python(value, o.context, o.ledger, o.check, o.sanitize, self.depth + 4)
                        else:
                            self.ordinary(value)
                    field = None
                else:
                    raise ValueError('submission nested shape withheld')
            # Keys and values are checked in their one event stream. The parser
            # still detects decoded duplicate keys BEFORE attempting their values.
            _EvidenceJSON(text, o.ledger, o.check).scan('{', None, depth=self.depth + (2 if stdout else 3), event=compare)
            if frames:
                raise ValueError('submission unresolved equality withheld')
        finally:
            text = None

    def close(self) -> bool:
        o, m, p, f, provider = self.owner, self.metadata, self.payload, self.file, self.provider
        if m.get('action') in ('jobs-block', 'jobs-final'):
            return self.completion_close()
        if m.get('action') == 'jobs-read' and m.get('operation') in ('receipt', 'output', 'wait', 'tail'):
            return self.read_close()
        selected = m.get('action') in ('jobs-submit', 'jobs-refuse-review')
        if not selected:
            # Deferred locations are ordinary unless this complete family wins.
            for r in self.receipts:
                if type(r['job'].get('script_text')) is str:
                    self.ordinary(r['job']['script_text'])
                if type(r['detail'].get('script_text')) is str:
                    self.ordinary(r['detail']['script_text'])
            for value in (m.get('stdout'), p.get('script'), f.get('text')):
                if type(value) is str:
                    self.ordinary(value)
            return False
        o.ledger.charge(1024, 128, o.check)
        def shape(path: tuple, kind: str, keys: tuple | None = None) -> bool:
            got = self.shapes.get(path)
            return (got is not None and got[0] == kind and
                    (keys is None or len(got[1]) == len(keys) and set(got[1]) == set(keys)))
        refusal = m['action'] == 'jobs-refuse-review'
        if (self.deferred or not shape((), 'object', self.fields)
                or not shape(('provider',), 'object', ('task', 'session', 'agent', 'org', 'pid'))
                or not shape(('payload',), 'object', self.payload_fields)
                or not shape(('payload_file',), 'object', ('path', 'text', 'bytes', 'sha256'))
                or not shape(('argv',), 'array') or not shape(('wire',), 'array')
                or m.get('operation', False) is not None or type(m.get('exit')) is not int
                or m['exit'] != (2 if refusal else 0) or m.get('timed_out') is not False
                or any(type(m.get(k)) is not str for k in ('stdout', 'stderr', 'python', 'source_sha', 'callback_sha256', 'child_stat', 'child_start'))
                or any(type(m.get(k)) not in (int, float) or not math.isfinite(m[k]) or m[k] < 0
                       for k in ('entered', 'finished', 'execution_deadline'))
                or not m['entered'] <= m['finished'] < m['execution_deadline']
                or any(type(m.get(k)) is not int or m[k] <= 0 for k in ('child_pid', 'child_parent'))
                or type(provider.get('pid')) is not int or provider['pid'] <= 0
                or m['child_parent'] != provider['pid']
                or any(type(provider.get(k)) is not str or not provider[k] for k in ('task', 'session', 'agent', 'org'))
                or provider['org'] != 'alpha' or provider['agent'] != 'case_manager'
                or not provider['task'].startswith('TASK-') or not provider['task'][5:].isascii() or not provider['task'][5:].isdigit()
                or any(type(p.get(k)) is not str or not p[k] for k in ('title', 'rationale', 'script', 'interpreter', 'task_id', 'session_id'))
                or p['interpreter'] != 'python3' or p.get('cwd_hint', False) is not None
                or p.get('review_required') is not refusal or p.get('persistent') is not False
                or type(p.get('max_runtime_seconds')) is not int or p['max_runtime_seconds'] not in (5, 30)
                or p['task_id'] != provider['task'] or p['session_id'] != provider['session']
                or type(f.get('path')) is not str or type(f.get('text')) is not str
                or type(f.get('bytes')) is not int or type(f.get('sha256')) is not str
                or not _failure_source(m['source_sha']) or len(m['callback_sha256']) != 64
                or any(c not in '0123456789abcdef' for c in m['callback_sha256'])):
            raise ValueError('submission producer identity withheld')
        # Original /proc spelling is retained. Only indispensable child identity
        # fields are inspected; this is no liveness/process lookup.
        stat_text = m['child_stat']
        o.ledger.charge(3 * _evidence_utf8_size(stat_text, o.check) + 64, 64, o.check)
        close = stat_text.rfind(')')
        fields, cursor = [], close + 2
        while close >= 0 and cursor < len(stat_text) and len(fields) < 20:
            while cursor < len(stat_text) and stat_text[cursor].isspace():
                cursor += 1
            begin = cursor
            while cursor < len(stat_text) and not stat_text[cursor].isspace():
                cursor += 1
                if cursor % 1024 == 0:
                    o.check()
            if cursor > begin:
                fields.append(stat_text[begin:cursor])
        if (not stat_text.startswith(str(m['child_pid']) + ' (') or len(fields) < 20
                or fields[1] != str(provider['pid']) or fields[19] != m['child_start']
                or not m['child_start'].isascii() or not m['child_start'].isdigit()):
            raise ValueError('submission child identity withheld')
        # Each occurrence pays its own source scan; the file occurrence is not
        # decoded until its complete sorted JSON stage below.
        _evidence_python(p['script'], o.context, o.ledger, o.check, o.sanitize, self.depth + 2)
        count, digest = _evidence_digest(f['text'], o.ledger, o.check)
        if count != f['bytes'] or digest != f['sha256']:
            raise ValueError('submission original file identity withheld')
        self.object(f['text'], p, script=True)
        if refusal:
            o.ledger.charge(2 * _evidence_utf8_size(f['path'], o.check) + 512, 1, o.check)
            expected_error = ('Error reading scripts-submit file ' + f['path'] +
                ': review_required_removed: obtain any required authorization in a thread before submitting; '
                'upgrade old clients and omit review_required or set it to false. This request was not submitted.\n')
            if m['stdout'] != '' or m['stderr'] != expected_error or ('wire', 0) in self.shapes:
                raise ValueError('submission local refusal withheld')
        else:
            response_keys = self.result_fields + (('deferred', 'code') if 'deferred' in self.response else ())
            if (m['stderr'] != '' or not shape(('wire', 0), 'object', ('path', 'method', 'body_sha256', 'status', 'response', 'dropped'))
                    or not shape(('wire', 0, 'response'), 'object', response_keys)
                    or not shape(('wire', 0, 'response', 'authentication'), 'object', ('task_id', 'session_id'))
                    or self.wire.get('path') != '/api/v1/orgs/alpha/jobs/submit' or self.wire.get('method') != 'POST'
                    or type(self.wire.get('status')) is not int or self.wire['status'] != 201 or self.wire.get('dropped') is not False
                    or type(self.wire.get('body_sha256')) is not str or len(self.wire['body_sha256']) != 64
                    or self.wire['body_sha256'] != _evidence_submit_digest(p, o.ledger, o.check)
                    or self.authentication != {'task_id': provider['task'], 'session_id': provider['session']}
                    or any(type(self.authentication.get(k)) is not str for k in ('task_id', 'session_id'))):
                raise ValueError('submission wire identity withheld')
            r = self.response
            if (type(r.get('id')) is not str or not r['id'].startswith('JOB-')
                    or not r['id'][4:].isascii() or not r['id'][4:].isdigit()
                    or type(r.get('created_at')) is not str or not r['created_at']
                    or ('deferred' in r and (r['deferred'] is not True or type(r.get('code')) is not str or not r['code'] or r.get('status') != 'pending'))
                    or ('deferred' not in r and r.get('status') != 'running')
                    or any(type(r.get(k)) not in (str, type(None)) for k in ('started_at', 'cwd_resolved', 'events_url'))
                    or type(r.get('timeout_seconds')) not in (int, type(None))
                    or r.get('timeout_seconds') not in (None, p['max_runtime_seconds'])
                    or r.get('events_url') not in (None, '/api/v1/orgs/alpha/jobs/' + r['id'] + '/events')
                    or 'deferred' in r and any(r.get(k) is not None for k in ('started_at','cwd_resolved','timeout_seconds','events_url'))
                    or 'deferred' not in r and (not r.get('started_at') or not r.get('cwd_resolved')
                        or r.get('timeout_seconds') != p['max_runtime_seconds'] or not r.get('events_url'))):
                raise ValueError('submission public result withheld')
            r['authentication'] = self.authentication
            self.object(m['stdout'], r, script=False, stdout=True)
        o.ledger.charge(128, 16, o.check)
        o.pending.append((provider, m['source_sha'], m['python'], m['callback_sha256'],
                          self.argv, f['path'], f['sha256'], m['child_pid'], self.response.get('id'), None))
        # An actual attempt may precede or follow its command. Retain only the
        # closed submit facts needed for that join, never another command tree.
        self.remember(p, 0 if refusal else 1)
        if not refusal:
            o.ledger.charge(512, 64, o.check)
            proof = dict(task_id=p['task_id'], agent_name=provider['agent'], title=p['title'], rationale=p['rationale'],
                         script_text=p['script'], interpreter=p['interpreter'], cwd_hint=p['cwd_hint'],
                         review_required=p['review_required'], persistent=p['persistent'], max_runtime_seconds=p['max_runtime_seconds'])
            o.job_proofs.append((provider['task'], self.response['id'], proof))
        self.payload = {}
        # Only owner identities and closed submit-payload references remain
        # until root/attempt closure; other command scratch is released now.
        self.provider, self.argv = {}, []
        self.release()
        return True

    def remember(self, payload: dict | None, wire_count: int, *, job: str | None = None) -> None:
        o, m = self.owner, self.metadata
        o.ledger.charge(160 + (16 * len(payload) if payload is not None else 0),
                        20 + (2 * len(payload) if payload is not None else 0), o.check)
        o.submitted[o.command_index] = (self.provider, payload, m['action'], m['entered'], m['finished'],
                                        m['execution_deadline'], wire_count, m['operation'], job,
                                        self.shapes.get(('payload',), [None, []])[1])

    def completion_close(self) -> bool:
        o, m, p, f, provider = self.owner, self.metadata, self.payload, self.file, self.provider
        self.command_identity()
        o.ledger.charge(2048, 256, o.check)
        def shape(path: tuple, kind: str, keys: tuple | None = None) -> bool:
            got = self.shapes.get(path)
            return got is not None and got[0] == kind and (keys is None or len(got[1]) == len(keys) and set(got[1]) == set(keys))
        blocked = m['action'] == 'jobs-block'
        required = ('status', 'confidence', 'summary', 'task_id', 'session_id', 'agent') + (('waiting_on_job_ids',) if blocked else ('decision',))
        got = self.shapes.get(('payload',), ['', []])[1]
        if (self.deferred or self.read_invalid or len(self.receipts) != 1
                or m.get('operation', False) is not None or not shape(('payload',), 'object')
                or any(k not in got for k in required)
                or any(k not in required + ('verdict',) + (('decision',) if blocked else ()) for k in got)
                or not shape(('payload_file',), 'object', ('path', 'text', 'bytes', 'sha256'))
                or type(p.get('confidence')) is not int or p['confidence'] != 90
                or p.get('status') != ('blocked' if blocked else 'completed')
                or any(type(p.get(k)) is not str or not p[k] for k in ('summary','task_id','session_id','agent'))
                or p['task_id'] != provider['task'] or p['session_id'] != provider['session'] or p['agent'] != provider['agent']
                or 'verdict' in got and p.get('verdict', False) is not None
                or blocked and 'decision' in got and p.get('decision', False) is not None
                or type(f.get('path')) is not str or type(f.get('text')) is not str
                or type(f.get('bytes')) is not int or type(f.get('sha256')) is not str):
            raise ValueError('completion producer identity withheld')
        if blocked:
            o.ledger.charge(32 * len(self.waits), 2 * len(self.waits), o.check)
            if (not shape(('payload', 'waiting_on_job_ids'), 'array') or not self.waits
                    or any(type(v) is not str or not v.startswith('JOB-') or not v[4:].isascii() or not v[4:].isdigit() for v in self.waits)
                    or len(set(self.waits)) != len(self.waits)):
                raise ValueError('completion waiting identity withheld')
            p['waiting_on_job_ids'] = self.waits
        else:
            if (not shape(('payload', 'decision'), 'object', ('action', 'summary'))
                    or self.decision.get('action') != 'done' or type(self.decision.get('summary')) is not str
                    or self.decision['summary'] != p['summary']):
                raise ValueError('completion done identity withheld')
            p['decision'] = self.decision
        count, digest = _evidence_digest(f['text'], o.ledger, o.check)
        if (count != f['bytes'] or digest != f['sha256']):
            raise ValueError('completion original file identity withheld')
        self.object(f['text'], p, script=False, completion=True)
        # tasks.cmd_report_completion sends no discovery GET. The wire is the
        # complete actual callback, unlike jobs-read's discovery+owned request.
        actual = self.receipts[0]
        o.ledger.charge(512 + 4 * _evidence_utf8_size(provider['task'], o.check), 64, o.check)
        body = dict(session_id=p['session_id'], agent=p['agent'], status=p['status'], confidence=p['confidence'],
                    output_summary=p['summary'], risks_flagged=[], dependencies=[], suggested_reviewer_focus=[])
        if not blocked:
            body['decision'] = p['decision']
        if blocked:
            body['waiting_on_job_ids'] = p['waiting_on_job_ids']
        if (actual['invalid'] or not shape(('wire', 0), 'object', ('path', 'method', 'body_sha256', 'status', 'response', 'dropped'))
                or self.shapes.get(('wire', 0, 'response')) != ['object', ['ok']]
                or actual['detail'] != {'ok': True} or actual['detail'].get('ok') is not True
                or self.wire.get('path') != '/api/v1/orgs/alpha/tasks/' + provider['task'] + '/completion'
                or self.wire.get('method') != 'POST' or type(self.wire.get('status')) is not int or self.wire['status'] != 200
                or self.wire.get('dropped') is not False
                or self.wire.get('body_sha256') != _evidence_submit_digest(body, o.ledger, o.check)):
            raise ValueError('completion complete wire identity withheld')
        self.ordinary(m['stdout']); self.ordinary(m['stderr'])
        o.ledger.charge(128, 16, o.check)
        o.pending.append((provider, m['source_sha'], m['python'], m['callback_sha256'],
                          self.argv, f['path'], f['sha256'], m['child_pid'], None, m['action']))
        self.remember(p, 1)
        self.provider, self.argv, self.payload, self.decision, self.waits = {}, [], {}, {}, []
        return True

    receipt_fields = ('authentication', 'job', 'output')
    job_fields = ('id', 'task_id', 'agent_name', 'title', 'rationale', 'script_text', 'interpreter',
                  'cwd_hint', 'cwd_resolved', 'status', 'exit_code', 'reason', 'duration_ms',
                  'created_at', 'started_at', 'finished_at')
    output_fields = ('stdout', 'stderr', 'truncated_stdout', 'truncated_stderr',
                     'total_stdout_bytes', 'total_stderr_bytes')
    detail_fields = ('id', 'task_id', 'agent_name', 'title', 'rationale', 'script_text', 'interpreter',
                     'cwd_hint', 'status', 'exit_code', 'stdout_head', 'stderr_head', 'stdout_path',
                     'stderr_path', 'duration_ms', 'started_at', 'finished_at', 'reviewed_at',
                     'reviewed_by', 'reject_reason', 'cwd_resolved', 'max_runtime_seconds',
                     'max_output_bytes', 'review_required', 'persistent', 'reason', 'created_at')

    def read_event(self, path: tuple, kind: str, value: object) -> None:
        """Only two legacy wire envelopes and closed receipt scalar facts.

        Discovery stays ordinary scan-only evidence. No general response tree
        or value index is retained, even when its path arrives after response.
        """
        if not path:
            return
        o = self.owner
        index = path[0]
        if type(index) is not int or index not in (0, 1):
            self.read_invalid = True
            if (kind == 'scalar' and path[1:] in (('response', 'job', 'script_text'), ('response', 'script_text'))
                    and type(value) is str):
                self.ordinary(value)
            return
        if len(path) == 1 and kind == 'object':
            if index != len(self.receipts):
                self.read_invalid = True
                return
            o.ledger.charge(192, 24, o.check)
            self.receipts.append(dict(meta={}, authentication={}, job={}, output={}, detail={}, lines=[], shapes={}, invalid=False))
        if index >= len(self.receipts):
            self.read_invalid = True
            return
        r, local = self.receipts[index], path[1:]
        named = ((), ('response',), ('response', 'authentication'), ('response', 'job'), ('response', 'output'), ('response', 'lines'))
        if kind in ('object', 'array'):
            if local in named:
                o.ledger.charge(24, 3, o.check)
                r['shapes'][local] = [kind, []]
            elif local[:2] not in (('response', 'orgs'), ('response', 'broken')):
                r['invalid'] = True
        elif kind == 'key' and local in r['shapes']:
            o.ledger.charge(16 + _evidence_utf8_size(value, o.check), 2, o.check)
            r['shapes'][local][1].append(value)
        elif kind == 'scalar':
            target = None
            if len(local) == 1 and local[0] in ('path', 'method', 'body_sha256', 'status', 'dropped'):
                target = r['meta']
            elif len(local) == 2 and local[0] == 'response' and local[1] in self.detail_fields + ('timed_out', 'stream', 'ok'):
                target = r['detail']
            elif len(local) == 3 and local[:2] == ('response', 'lines') and local[2] == len(r['lines']):
                o.ledger.charge(16 + (_evidence_utf8_size(value, o.check) if type(value) is str else 8), 2, o.check)
                r['lines'].append(value)
            elif len(local) == 3 and local[0] == 'response' and local[1] in self.receipt_fields:
                allowed = ('task_id', 'session_id') if local[1] == 'authentication' else self.job_fields if local[1] == 'job' else self.output_fields
                if local[2] in allowed:
                    target = r[local[1]]
            if target is not None:
                o.ledger.charge(16 + (_evidence_utf8_size(value, o.check) if type(value) is str else 8), 2, o.check)
                target[local[-1]] = value

    def command_identity(self) -> None:
        o, m, p = self.owner, self.metadata, self.provider
        o.ledger.charge(2048, 256, o.check)
        def shape(shapes: dict, path: tuple, kind: str, keys: tuple | None = None) -> bool:
            got = shapes.get(path)
            return got is not None and got[0] == kind and (keys is None or got[1] == list(keys))
        if (not shape(self.shapes, (), 'object')
                or len(self.shapes[()][1]) != len(self.fields) or set(self.shapes[()][1]) != set(self.fields)
                or not shape(self.shapes, ('provider',), 'object', ('task', 'session', 'agent', 'org', 'pid'))
                or not shape(self.shapes, ('argv',), 'array') or not shape(self.shapes, ('wire',), 'array')
                or type(m.get('exit')) is not int or m['exit'] != 0 or m.get('timed_out') is not False
                or any(type(m.get(k)) is not str for k in ('stdout', 'stderr', 'python', 'source_sha', 'callback_sha256', 'child_stat', 'child_start'))
                or any(type(m.get(k)) not in (int, float) or not math.isfinite(m[k]) or m[k] < 0
                       for k in ('entered', 'finished', 'execution_deadline'))
                or not m['entered'] <= m['finished'] < m['execution_deadline']
                or any(type(m.get(k)) is not int or m[k] <= 0 for k in ('child_pid', 'child_parent'))
                or type(p.get('pid')) is not int or p['pid'] <= 0 or m['child_parent'] != p['pid']
                or any(type(p.get(k)) is not str or not p[k] for k in ('task', 'session', 'agent', 'org'))
                or p['org'] != 'alpha' or p['agent'] != 'case_manager'
                or not p['task'].startswith('TASK-') or not p['task'][5:].isascii() or not p['task'][5:].isdigit()
                or not _failure_source(m['source_sha']) or len(m['callback_sha256']) != 64
                or any(c not in '0123456789abcdef' for c in m['callback_sha256'])):
            raise ValueError('receipt command identity withheld')
        stat_text = m['child_stat']
        o.ledger.charge(3 * _evidence_utf8_size(stat_text, o.check) + 64, 64, o.check)
        close = stat_text.rfind(')')
        fields, cursor = [], close + 2
        while close >= 0 and cursor < len(stat_text) and len(fields) < 20:
            while cursor < len(stat_text) and stat_text[cursor].isspace():
                cursor += 1
            begin = cursor
            while cursor < len(stat_text) and not stat_text[cursor].isspace():
                cursor += 1
                if cursor % 1024 == 0:
                    o.check()
            if cursor > begin:
                fields.append(stat_text[begin:cursor])
        if (not stat_text.startswith(str(m['child_pid']) + ' (') or len(fields) < 20
                or fields[1] != str(p['pid']) or fields[19] != m['child_start']
                or not m['child_start'].isascii() or not m['child_start'].isdigit()):
            raise ValueError('receipt child identity withheld')

    def read_close(self) -> bool:
        o, m, p = self.owner, self.metadata, self.provider
        o.ledger.charge(2048, 256, o.check)
        def shape(shapes: dict, path: tuple, kind: str, keys: tuple | None = None) -> bool:
            got = shapes.get(path)
            # This finite set comparison is reserved above, before allocation.
            return got is not None and got[0] == kind and (keys is None or got[1] == list(keys))
        self.command_identity()
        if (self.read_invalid or len(self.receipts) != 2
                or m.get('payload', False) is not None or m.get('payload_file', False) is not None
                or m['operation'] in ('receipt', 'output') and m['stderr'] != ''):
            raise ValueError('receipt command identity withheld')
        discovery, actual = self.receipts
        wire_fields = ('path', 'method', 'body_sha256', 'status', 'response', 'dropped')
        for r in self.receipts:
            w = r['meta']
            if (r['invalid'] or not shape(r['shapes'], (), 'object')
                    or len(r['shapes'][()][1]) != len(wire_fields) or set(r['shapes'][()][1]) != set(wire_fields)
                    or w.get('method') != ('POST' if r is actual and m['operation'] == 'wait' else 'GET')
                    or type(w.get('status')) is not int or w['status'] != 200
                    or w.get('dropped') is not False or w.get('body_sha256') != hashlib.sha256(b'').hexdigest()):
                raise ValueError('receipt wire envelope withheld')
        if (discovery['meta'].get('path') != '/api/v1/orgs'
                or not shape(discovery['shapes'], ('response',), 'object', ('orgs', 'broken'))
                or any(discovery[k] for k in self.receipt_fields)):
            raise ValueError('receipt discovery cannot authenticate job')
        if m['operation'] in ('wait', 'tail'):
            return self.other_read_close(actual)
        j, output, authentication = actual['job'], actual['output'], actual['authentication']
        if (not shape(actual['shapes'], ('response',), 'object', self.receipt_fields)
                or not shape(actual['shapes'], ('response', 'authentication'), 'object', ('task_id', 'session_id'))
                or not shape(actual['shapes'], ('response', 'job'), 'object', self.job_fields)
                or not shape(actual['shapes'], ('response', 'output'), 'object', self.output_fields)
                or set(j) != set(self.job_fields) or set(output) != set(self.output_fields)
                or authentication != dict(task_id=p['task'], session_id=p['session'])
                or any(type(authentication.get(k)) is not str for k in ('task_id', 'session_id'))
                or any(type(j.get(k)) is not str for k in ('id', 'task_id', 'agent_name', 'title', 'rationale', 'script_text', 'interpreter', 'status', 'created_at'))
                or not j['id'].startswith('JOB-') or not j['id'][4:].isascii() or not j['id'][4:].isdigit()
                or j['task_id'] != p['task'] or j['agent_name'] != p['agent'] or j['interpreter'] != 'python3'
                or j['status'] not in ('completed', 'failed', 'rejected') or not j['created_at']
                or any(type(j.get(k)) not in (str, type(None)) for k in ('cwd_hint', 'cwd_resolved', 'reason', 'started_at', 'finished_at'))
                or type(j.get('exit_code')) not in (int, type(None))
                or type(j.get('duration_ms')) not in (int, type(None))
                or j['duration_ms'] is not None and j['duration_ms'] < 0
                or j['status'] == 'completed' and (type(j['exit_code']) is not int or type(j['duration_ms']) is not int
                    or not j['started_at'] or not j['finished_at'] or not j['cwd_resolved'] or j['reason'] is not None)
                or j['status'] == 'rejected' and (j['exit_code'] is not None or j['duration_ms'] is not None)
                or j['reason'] == 'timeout' and (j['status'] != 'failed' or j['exit_code'] != -9)):
            raise ValueError('receipt closed identity withheld')
        # HTTPX preserves these actual CLI parameter insertion orders. Runtime
        # task/session identifiers use unreserved ASCII, so no URL rewrite is
        # performed. Unsupported spellings refuse rather than normalize.
        for value in (p['task'], p['session']):
            o.ledger.charge(2 * _evidence_utf8_size(value, o.check), 1, o.check)
            if not value.isascii() or any(not (c.isalnum() or c in '-._~') for c in value):
                raise ValueError('receipt query spelling withheld')
        o.ledger.charge(512 + 4 * sum(_evidence_utf8_size(v, o.check) for v in (p['task'], p['session'], j['id'])), 8, o.check)
        path = '/api/v1/orgs/alpha/jobs/' + j['id'] + '/receipt?task_id=' + p['task'] + '&session_id=' + p['session']
        if m['operation'] == 'output':
            path += '&stream=both&max_bytes=1048576'
        if actual['meta'].get('path') != path:
            raise ValueError('receipt exact operation query withheld')
        for stream in ('stdout', 'stderr'):
            text, truncated, total = output.get(stream), output.get('truncated_' + stream), output.get('total_' + stream + '_bytes')
            if type(text) is not str or type(truncated) is not bool or type(total) is not int or total < 0 or truncated != (total > 1048576):
                raise ValueError('receipt output types withheld')
            size = _evidence_utf8_size(text, o.check)
            o.ledger.charge(size, 1, o.check)
            read_bytes = min(total, 1048576)
            # The producer decodes with errors=replace. Without a replacement
            # scalar UTF8 counts are exact; with one, only this truthful bound
            # is observable without acquiring the original output file again.
            if size < read_bytes or size > 3 * read_bytes or '\ufffd' not in text and size != read_bytes:
                raise ValueError('receipt output byte relationship withheld')
            self.ordinary(text)
        _evidence_python(j['script_text'], o.context, o.ledger, o.check, o.sanitize, self.depth + 4)
        expected = dict(authentication=authentication, job=j, output=output)
        self.object(m['stdout'], expected, script=False, stdout=True, receipt=True)
        o.ledger.charge(128, 16, o.check)
        o.pending.append((p, m['source_sha'], m['python'], m['callback_sha256'],
                          self.argv, None, None, m['child_pid'], j['id'], m['operation']))
        o.ledger.charge(128, 16, o.check)
        o.job_proofs.append((p['task'], j['id'], j))
        actual['job'] = {}  # Transfer these closed facts, never a raw response tree.
        self.remember(None, len(self.receipts), job=j['id'])
        self.provider, self.argv = {}, []
        return True

    def other_read_close(self, actual: dict) -> bool:
        """Only the actual wait JobRecord and tail line response; show deferred."""
        o, m, p, operation = self.owner, self.metadata, self.provider, self.metadata['operation']
        o.ledger.charge(2048, 256, o.check)
        def shape(path: tuple, kind: str, keys: tuple | None = None) -> bool:
            got = actual['shapes'].get(path)
            return got is not None and got[0] == kind and (keys is None or got[1] == list(keys))
        if (len(self.argv) < 4 or type(self.argv[3]) is not str or not self.argv[3].startswith('JOB-')
                or not self.argv[3][4:].isascii() or not self.argv[3][4:].isdigit()
                or any(actual[k] for k in self.receipt_fields)):
            raise ValueError('read actual job identity withheld')
        job_id = self.argv[3]
        for value in (p['task'], p['session']):
            o.ledger.charge(2 * _evidence_utf8_size(value, o.check), 1, o.check)
            if not value.isascii() or any(not (c.isalnum() or c in '-._~') for c in value):
                raise ValueError('read query spelling withheld')
        o.ledger.charge(512 + 4 * sum(_evidence_utf8_size(v, o.check) for v in (job_id, p['task'], p['session'])), 8, o.check)
        query = ('timeout_seconds=1' if operation == 'wait' else 'stream=stdout&lines=50')
        path = '/api/v1/orgs/alpha/jobs/' + job_id + '/' + operation + '?' + query + '&task_id=' + p['task'] + '&session_id=' + p['session']
        if actual['meta'].get('path') != path:
            raise ValueError('read exact operation query withheld')
        j = actual['detail']
        if operation == 'wait':
            if (not shape(('response',), 'object', self.detail_fields + ('timed_out',))
                    or set(j) != set(self.detail_fields + ('timed_out',))
                    or any(type(j.get(k)) is not str for k in ('id','task_id','agent_name','title','rationale','script_text','interpreter','status','created_at'))
                    or j['id'] != job_id or j['task_id'] != p['task'] or j['agent_name'] != p['agent']
                    or j['interpreter'] != 'python3' or j['status'] not in ('pending','running','completed','failed','rejected')
                    or any(type(j.get(k)) not in (str, type(None)) for k in ('cwd_hint','stdout_head','stderr_head','stdout_path','stderr_path',
                        'started_at','finished_at','reviewed_at','reviewed_by','reject_reason','cwd_resolved','reason'))
                    or any(type(j.get(k)) not in (int, type(None)) for k in ('exit_code','duration_ms','max_runtime_seconds','max_output_bytes'))
                    or type(j.get('review_required')) is not bool or type(j.get('persistent')) is not bool
                    or type(j.get('timed_out')) is not bool
                    or j['timed_out'] != (j['status'] not in ('completed','failed','rejected'))
                    or j['duration_ms'] is not None and j['duration_ms'] < 0):
                raise ValueError('wait full JobRecord identity withheld')
            _evidence_python(j['script_text'], o.context, o.ledger, o.check, o.sanitize, self.depth + 3)
            expected = dict(status=j['status'], timed_out=j['timed_out'])
            self.object(m['stdout'], expected, script=False, stdout=True, ordered=True)
            o.ledger.charge(32, 4, o.check)
            o.read_jobs.append((p['task'], job_id, j))
            actual['detail'] = {}
        else:
            if (not shape(('response',), 'object', ('stream', 'lines')) or not shape(('response','lines'), 'array')
                    or j != {'stream': 'stdout'} or any(type(line) is not str for line in actual['lines'])):
                raise ValueError('tail complete response withheld')
            size = sum(_evidence_utf8_size(line, o.check) + 1 for line in actual['lines'])
            o.ledger.charge(2 * size + 16 * len(actual['lines']), 2 * len(actual['lines']), o.check)
            if m['stdout'] != ''.join(line + '\n' for line in actual['lines']):
                raise ValueError('tail full literal equality withheld')
            self.ordinary(m['stdout'])
            o.ledger.charge(32, 4, o.check)
            o.read_jobs.append((p['task'], job_id, None))
        self.ordinary(m['stderr'])
        o.ledger.charge(160, 20, o.check)
        o.pending.append((p, m['source_sha'], m['python'], m['callback_sha256'],
                          self.argv, None, None, m['child_pid'], job_id, operation))
        self.remember(None, 2, job=job_id)
        self.provider, self.argv = {}, []
        return True

    def release(self) -> None:
        for r in self.receipts:
            for value in r.values():
                if type(value) is dict:
                    value.clear()
            r.clear()
        self.receipts.clear()
        for value in (self.metadata, self.payload, self.provider, self.file, self.response,
                      self.authentication, self.wire, self.argv, self.shapes, self.decision, self.waits):
            value.clear()


class _EvidenceObservations:
    """Selected canonical ledger + existing start/executable identity facts.

    Raw input remains scan-only. These finite joins are indispensable to the
    selected record and can arrive in either order; there is no full raw tree.
    """
    def __init__(self, context: tuple | list, ledger: _EvidenceLedger,
                 check: Callable[[], None], sanitize: Callable[[str], str], depth: int,
                 identity: tuple | None) -> None:
        ledger.charge(128, 16, check)
        self.context, self.ledger, self.check, self.sanitize, self.depth = context, ledger, check, sanitize, depth
        self.identity = identity
        self.starts, self.manifests, self.pending = [], [], []
        self.attempts, self.submitted = [], {}
        self.job_proofs, self.read_jobs = [], []
        self.attempt = None
        self.command_index = None
        self.current = self.start = self.manifest = None
        self.captured = False

    @staticmethod
    def encoded(path: tuple) -> bool:
        return (len(path) == 3 and path[0] == 'provider_commands' and path[2] == 'stdout'
                or len(path) == 4 and path[0] == 'provider_commands'
                    and path[2:] in (('payload', 'script'), ('payload_file', 'text'))
                or len(path) == 7 and path[0] == 'provider_commands' and path[2] == 'wire'
                    and path[4:] == ('response', 'job', 'script_text')
                or len(path) == 6 and path[0] == 'provider_commands' and path[2] == 'wire'
                    and path[4:] == ('response', 'script_text')
                or len(path) == 5 and path[0] == 'provider_attempts'
                    and path[2:] == ('instruction', 'payload', 'script'))

    def attempt_event(self, path: tuple, kind: str, value: object) -> None:
        """Only source-owned submit-plan facts; no raw attempt value tree."""
        if len(path) == 1:
            if kind not in ('array', 'end-array'):
                raise ValueError('attempt ledger shape withheld')
            return
        if len(path) == 2 and kind == 'object':
            if self.attempt is not None:
                raise ValueError('attempt overlapping owner withheld')
            self.ledger.charge(192, 24, self.check)
            self.attempt = dict(metadata={}, provider={}, instruction={}, payload={}, decision={}, waits=[], shapes={}, invalid=False)
        if self.attempt is None:
            raise ValueError('attempt record shape withheld')
        a, local = self.attempt, path[2:]
        if kind in ('object', 'array'):
            if local in ((), ('provider',), ('instruction',), ('instruction', 'payload'),
                         ('instruction','payload','decision'), ('instruction','payload','waiting_on_job_ids')):
                self.ledger.charge(24, 3, self.check)
                a['shapes'][local] = [kind, []]
            else:
                a['invalid'] = True
        elif kind == 'key' and local in a['shapes']:
            self.ledger.charge(16 + _evidence_utf8_size(value, self.check), 2, self.check)
            a['shapes'][local][1].append(value)
        elif kind == 'scalar':
            target = None
            if len(local) == 1 and local[0] in ('entered', 'wire_begin', 'wire_end', 'finished', 'command_index'):
                target = a['metadata']
            elif len(local) == 2 and local[0] == 'provider' and local[1] in ('task','session','agent','org','pid','pid_start','pgid'):
                target = a['provider']
            elif len(local) == 2 and local[0] == 'instruction' and local[1] in ('action','deadline','execution_deadline','operation','job'):
                target = a['instruction']
            elif len(local) == 3 and local[:2] == ('instruction', 'payload') and local[2] in _EvidenceSubmission.plan_fields + _EvidenceSubmission.completion_fields:
                target = a['payload']
            elif len(local) == 4 and local[:3] == ('instruction','payload','decision') and local[3] in ('action','summary'):
                target = a['decision']
            elif len(local) == 4 and local[:3] == ('instruction','payload','waiting_on_job_ids') and local[3] == len(a['waits']):
                self.ledger.charge(16 + (_evidence_utf8_size(value, self.check) if type(value) is str else 8), 2, self.check)
                a['waits'].append(value)
            if target is not None:
                self.ledger.charge(16 + (_evidence_utf8_size(value, self.check) if type(value) is str else 8), 2, self.check)
                target[local[-1]] = value
        if len(path) == 2 and kind == 'end-object':
            if (a['instruction'].get('action') in ('jobs-submit', 'jobs-refuse-review')
                    or any(k in a['metadata'] for k in ('command_index','wire_end','finished'))):
                self.ledger.charge(8, 1, self.check)
                self.attempts.append(a)
            elif type(a['payload'].get('script')) is str:
                # No familiar script key or unsupported action grants a stage.
                text = a['payload']['script']
                if '\\' in text:
                    raise ValueError('attempt ordinary representation withheld')
                _evidence_original(text, self.context, self.ledger, self.check, self.sanitize)
            self.attempt = None

    def close_attempts(self) -> bool:
        """Resolve every retained attempt once before root publication."""
        self.ledger.charge(16, 1, self.check)
        seen = set()
        for a in self.attempts:
            self.ledger.charge(2048, 256, self.check)
            def shape(path: tuple, keys: tuple) -> bool:
                got = a['shapes'].get(path)
                return got is not None and got[0] == 'object' and len(got[1]) == len(keys) and set(got[1]) == set(keys)
            m, p, instruction, payload = (a[k] for k in ('metadata','provider','instruction','payload'))
            if (a['invalid'] or not shape((), ('provider','instruction','entered','wire_begin','wire_end','finished','command_index'))
                    or not shape(('provider',), ('task','session','agent','org','pid','pid_start','pgid'))
                    or type(m.get('command_index')) is not int or m['command_index'] < 0 or m['command_index'] in seen
                    or m['command_index'] not in self.submitted
                    or any(type(m.get(k)) is not int or m[k] < 0 for k in ('wire_begin','wire_end'))
                    or m['wire_end'] < m['wire_begin']
                    or any(type(m.get(k)) not in (int,float) or not math.isfinite(m[k]) or m[k] < 0 for k in ('entered','finished'))
                    or any(type(instruction.get(k)) not in (int,float) or not math.isfinite(instruction[k]) or instruction[k] < 0
                           for k in ('deadline','execution_deadline'))):
                raise ValueError('attempt producer identity withheld')
            provider, submitted, action, entered, finished, end, wire_count, operation, job, order = self.submitted[m['command_index']]
            reading = action == 'jobs-read'
            instruction_fields = ('action','deadline','execution_deadline') + (('operation','job') if reading else ('payload',))
            if not shape(('instruction',), instruction_fields):
                raise ValueError('attempt closed instruction withheld')
            self.ledger.charge(128 + 32 * len(self.starts), 16 + 4 * len(self.starts), self.check)
            def same(left: object, right: object) -> bool:
                self.ledger.charge(32 + sum(_evidence_utf8_size(v, self.check) if type(v) is str else 8
                                          for v in (left,right)), 2, self.check)
                return type(left) is type(right) and left == right
            starts = [s for s in self.starts if same(s.get('session'), p.get('session'))]
            if (len(starts) != 1 or set(p) != set(starts[0])
                    or any(not same(p[k], starts[0][k]) for k in p)
                    or any(not same(p.get(k), v) for k,v in provider.items())
                    or instruction['action'] != action or type(instruction['execution_deadline']) is not type(end)
                    or instruction['execution_deadline'] != end
                    or instruction['deadline'] != end + 1
                    or not m['entered'] <= entered <= finished <= m['finished'] < instruction['deadline']
                    or m['wire_end'] - m['wire_begin'] != wire_count):
                raise ValueError('attempt complete owner join withheld')
            if reading:
                if not same(instruction.get('operation'), operation) or not same(instruction.get('job'), job):
                    raise ValueError('attempt exact read plan withheld')
            elif action in ('jobs-submit','jobs-refuse-review'):
                if not shape(('instruction','payload'), _EvidenceSubmission.plan_fields):
                    raise ValueError('attempt closed submit plan withheld')
            else:
                keys = [k for k in order if k not in ('task_id','session_id','agent')]
                if not shape(('instruction','payload'), tuple(keys)):
                    raise ValueError('attempt closed completion plan withheld')
                if 'decision' in keys and submitted.get('decision') is not None:
                    if not shape(('instruction','payload','decision'), ('action','summary')):
                        raise ValueError('attempt closed done shape withheld')
                    payload['decision'] = a['decision']
                if 'waiting_on_job_ids' in keys:
                    got = a['shapes'].get(('instruction','payload','waiting_on_job_ids'))
                    if got is None or got[0] != 'array':
                        raise ValueError('attempt closed waiting shape withheld')
                    payload['waiting_on_job_ids'] = a['waits']
            for key in (() if reading else _EvidenceSubmission.plan_fields if action in ('jobs-submit','jobs-refuse-review') else keys):
                expected, actual = submitted[key], payload.get(key)
                def equal(left: object, right: object) -> bool:
                    self.ledger.charge(32, 4, self.check)
                    if type(left) is not type(right):
                        return False
                    if type(right) is dict:
                        return list(left) == list(right) and all(equal(left[k], right[k]) for k in right)
                    if type(right) is list:
                        return len(left) == len(right) and all(equal(x, y) for x, y in zip(left, right))
                    return same(left, right)
                if not equal(actual, expected):
                    raise ValueError('attempt complete payload equality withheld')
            if action in ('jobs-submit','jobs-refuse-review'):
                _evidence_python(payload['script'], self.context, self.ledger, self.check, self.sanitize, self.depth + 5)
            self.ledger.charge(16, 1, self.check)
            seen.add(m['command_index'])
        return bool(self.attempts)

    def event(self, path: tuple, kind: str, value: object) -> None:
        self.ledger.charge(8, 1, self.check)
        if path[0] == 'provider_attempts':
            self.attempt_event(path, kind, value)
        elif path[0] == 'provider_commands':
            if len(path) == 1:
                if kind not in ('array', 'end-array'):
                    raise ValueError('submission ledger shape withheld')
                return
            if len(path) == 2 and kind == 'object':
                if self.current is not None:
                    raise ValueError('submission overlapping owner withheld')
                self.command_index = path[1]
                self.current = _EvidenceSubmission(self, self.depth + 2)
            if self.current is None:
                raise ValueError('submission command shape withheld')
            self.current.event(path[2:], kind, value)
            if len(path) == 2 and kind == 'end-object':
                try:
                    selected = self.current.close()
                    self.captured = selected or self.captured
                finally:
                    self.current.release()
                    self.current = None
        elif path[0] == 'starts':
            if len(path) == 2 and kind == 'object':
                self.ledger.charge(64, 8, self.check)
                self.start = {}
            elif len(path) == 2 and kind == 'key' and self.start is not None:
                self.ledger.charge(16 + _evidence_utf8_size(value, self.check), 2, self.check)
                self.start[value] = None
            elif len(path) == 3 and kind == 'scalar' and self.start is not None:
                self.ledger.charge(16 + (_evidence_utf8_size(value, self.check) if type(value) is str else 8), 2, self.check)
                self.start[path[2]] = value
            elif len(path) == 2 and kind == 'end-object' and self.start is not None:
                self.ledger.charge(8, 1, self.check)
                self.starts.append(self.start); self.start = None
        elif path[0] == 'durable' and len(path) >= 3 and path[2] == 'executable_manifest':
            if len(path) == 3 and kind == 'object':
                self.ledger.charge(64, 8, self.check)
                self.manifest = {}
            elif len(path) == 3 and kind == 'key' and self.manifest is not None:
                self.ledger.charge(16 + _evidence_utf8_size(value, self.check), 2, self.check)
                self.manifest[value] = None
            elif len(path) == 4 and path[3] == 'stubs' and kind == 'object' and self.manifest is not None:
                self.manifest['stubs'] = True
            elif len(path) == 4 and kind == 'scalar' and self.manifest is not None:
                self.ledger.charge(16 + (_evidence_utf8_size(value, self.check) if type(value) is str else 8), 2, self.check)
                self.manifest[path[3]] = value
            elif len(path) == 3 and kind == 'end-object' and self.manifest is not None:
                self.ledger.charge(8, 1, self.check)
                self.manifests.append(self.manifest); self.manifest = None

    def close(self) -> bool:
        try:
            attempts = self.close_attempts()
            if not self.captured and not attempts:
                return False
            if len(self.manifests) != 1:
                raise ValueError('submission executable owner withheld')
            manifest = self.manifests[0]
            if (set(manifest) != {'root', 'source', 'revision', 'python', 'callback', 'callback_sha256', 'stubs'}
                    or manifest['stubs'] is not True):
                raise ValueError('submission manifest identity withheld')
            if self.identity is None or self.identity != (manifest['root'], manifest['revision']):
                raise ValueError('submission original caller identity withheld')
            root = manifest['root']
            self.ledger.charge(1024 + 4 * _evidence_utf8_size(root, self.check), 128, self.check)
            if (type(root) is not str or not root.startswith('/') or root.endswith(('/', '/.', '/..')) or '//' in root or '/./' in root or '/../' in root
                    or '\\' in root or '\x00' in root or manifest['python'] != root + '/venv/bin/python'
                    or manifest['callback'] != root + '/bin/happyranch'
                    or type(manifest['source']) is not str or not manifest['source'].startswith('/')
                    or not _failure_source(manifest['revision'])):
                raise ValueError('submission executable identity withheld')
            children, jobs, closed = set(), set(), set()
            for p, source_sha, python, callback_sha256, argv, path, sha256, child_pid, job_id, read in self.pending:
                self.ledger.charge(32, 4, self.check)
                if child_pid in children or read is None and job_id is not None and job_id in jobs or p['session'] in closed:
                    raise ValueError('submission duplicate identity withheld')
                children.add(child_pid)
                if read is None and job_id is not None:
                    jobs.add(job_id)
                self.ledger.charge(128 + 32 * len(self.starts), 16 + 4 * len(self.starts), self.check)
                self.ledger.charge(128 + 2 * sum(_evidence_utf8_size(v, self.check) for v in argv if type(v) is str),
                                   16 + len(argv), self.check)
                if read is None or read in ('jobs-block', 'jobs-final'):
                    expected_argv = ([manifest['callback'], 'jobs', 'submit'] if read is None else [manifest['callback'], 'report-completion'])
                    expected_argv += ['--org', p['org'], '--from-file', path]
                    if read is None:
                        expected_argv += ['--json']
                    else:
                        self.ledger.charge(16, 1, self.check)
                        closed.add(p['session'])
                    if path != root + '/coord/' + sha256 + '.json':
                        raise ValueError('submission original path withheld')
                else:
                    expected_argv = [manifest['callback'], 'jobs', 'show' if read == 'receipt' else read, job_id, '--org', p['org']]
                    if read in ('receipt', 'output'):
                        expected_argv += ['--json']
                    if read == 'wait':
                        expected_argv += ['--timeout-seconds', '1']
                    expected_argv += ['--task-id', p['task'], '--session-id', p['session']]
                matching = [s for s in self.starts if s.get('session') == p['session']]
                if (len(matching) != 1 or set(matching[0]) != {'task', 'session', 'agent', 'org', 'pid', 'pid_start', 'pgid'}
                        or any(type(matching[0][k]) is not type(v) or matching[0][k] != v for k, v in p.items())
                        or type(matching[0]['pid_start']) is not str or not matching[0]['pid_start'].isdigit()
                        or type(matching[0]['pgid']) is not int or matching[0]['pgid'] != p['pid']
                        or source_sha != manifest['revision'] or python != manifest['python']
                        or callback_sha256 != manifest['callback_sha256']
                        or argv != expected_argv
                        or any(type(v) is not str for v in argv)):
                    raise ValueError('submission complete owner join withheld')
            for task, job_id, detail in self.read_jobs:
                found = False
                for proof_task, proof_job, proof in self.job_proofs:
                    self.ledger.charge(32, 4, self.check)
                    if proof_task != task or proof_job != job_id:
                        continue
                    found = True
                    if detail is not None:
                        for key, value in proof.items():
                            other = detail.get(key)
                            self.ledger.charge(32 + (_evidence_utf8_size(value, self.check) + _evidence_utf8_size(other, self.check)
                                if type(value) is str and type(other) is str else 16), 2, self.check)
                            if type(other) is not type(value) or other != value:
                                raise ValueError('read original job proof equality withheld')
                if not found:
                    raise ValueError('read original job proof unavailable')
            return True
        finally:
            self.release()

    def release(self) -> None:
        if self.current is not None:
            self.current.release(); self.current = None
        self.pending.clear(); self.starts.clear(); self.manifests.clear()
        self.attempts.clear(); self.submitted.clear(); self.attempt = None
        self.job_proofs.clear(); self.read_jobs.clear()
        self.start = self.manifest = None


class _EvidenceJSON:
    """Indexed strict grammar for required trees AND registered raw scans.

    Scan mode retains only active key/syntax state and the current scalar.
    Scalar preflight/decode is shared; no raw tree, full token plan or reparse.
    """
    def __init__(self, text: str, ledger: _EvidenceLedger, check: Callable[[], None]) -> None:
        self.text, self.ledger, self.check, self.index = text, ledger, check, 0
        self.end = len(text)
        self.relay_captured = False

    def error(self) -> None:
        # Do not retain the whole refused image in a JSONDecodeError diagnostic.
        raise json.JSONDecodeError("invalid evidence JSON", "", 0)

    def whitespace(self) -> None:
        while self.index < self.end and self.text[self.index] in " \t\r\n":
            if self.index % 4096 == 0:
                self.check()
            self.index += 1

    def codepoint(self, index: int) -> tuple[int, int]:
        if index >= self.end:
            self.error()
        code = ord(self.text[index])
        index += 1
        if code == 92:
            if index >= self.end:
                self.error()
            escape = self.text[index]
            index += 1
            if escape in '"\\/bfnrt':
                return ord('"\\/\b\f\n\r\t'['"\\/bfnrt'.index(escape)]), index
            if escape != "u":
                self.error()
            code, index = self.hex4(index)
            if 0xD800 <= code <= 0xDBFF:
                if index + 2 > self.end or self.text[index] != "\\" or self.text[index + 1] != "u":
                    self.error()
                low, index = self.hex4(index + 2)
                if not 0xDC00 <= low <= 0xDFFF:
                    self.error()
                code = 0x10000 + ((code - 0xD800) << 10) + low - 0xDC00
            elif 0xDC00 <= code <= 0xDFFF:
                self.error()
        elif code < 32 or 0xD800 <= code <= 0xDFFF:
            self.error()
        return code, index

    def hex4(self, index: int) -> tuple[int, int]:
        code = 0
        for _ in range(4):
            if index >= self.end or self.text[index] not in "0123456789abcdefABCDEF":
                self.error()
            code = code * 16 + int(self.text[index], 16)
            index += 1
        return code, index

    def string(self) -> str:
        if self.index >= self.end or self.text[self.index] != '"':
            self.error()
        start = self.index + 1
        cursor, size = start, 0
        while cursor < self.end and self.text[cursor] != '"':
            if cursor % 4096 == 0:
                self.check()
            code, cursor = self.codepoint(cursor)
            size += 1 if code < 128 else 2 if code < 2048 else 3 if code < 65536 else 4
            if size > 8388608:
                raise _EvidenceCapacity("JSON scalar capacity refused")
        if cursor >= self.end:
            self.error()
        # Charge decoded storage, chunk references, and join BEFORE decoding.
        chunks = (size + 1020) // 1021
        self.ledger.charge(8 + 2 * size + 8 * chunks, 1 + chunks, self.check)
        scratch = min(4096, 2 * size)
        self.ledger.reserve(scratch, 0, self.check)
        try:
            parts, chunk, chunk_size = [], "", 0
            index = start
            while index < cursor:
                code, index = self.codepoint(index)
                width = 1 if code < 128 else 2 if code < 2048 else 3 if code < 65536 else 4
                if chunk_size + width > 1024:
                    parts.append(chunk)
                    chunk, chunk_size = "", 0
                    self.check()
                chunk += chr(code)
                chunk_size += width
            if chunk:
                parts.append(chunk)
            value = "".join(parts)
            self.index = cursor + 1
        finally:
            self.ledger.settle(scratch, 0)  # Only scratch, never decoded work/facts.
        self.check()
        return value

    def number(self) -> int | float:
        start = self.index
        if self.text[self.index] == "-":
            self.index += 1
        if self.index >= self.end:
            self.error()
        if self.text[self.index] == "0":
            self.index += 1
        elif self.text[self.index] in "123456789":
            while self.index < self.end and self.text[self.index] in "0123456789":
                self.index += 1
                if self.index - start > 128:
                    raise _EvidenceCapacity("JSON number spelling refused")
        else:
            self.error()
        floating = False
        for delimiter, signs in ((".", False), ("eE", True)):
            if self.index < self.end and self.text[self.index] in delimiter:
                floating = True
                self.index += 1
                if signs and self.index < self.end and self.text[self.index] in "+-":
                    self.index += 1
                digits = self.index
                while self.index < self.end and self.text[self.index] in "0123456789":
                    self.index += 1
                    if self.index - start > 128:
                        raise _EvidenceCapacity("JSON number spelling refused")
                if self.index == digits:
                    self.error()
        length = self.index - start
        if length > (128 if floating else 21):
            raise _EvidenceCapacity("JSON number spelling refused")
        self.ledger.charge(length + 8, 1, self.check)
        spelling = "".join(self.text[i] for i in range(start, self.index))
        value = float(spelling) if floating else int(spelling)
        if (floating and not math.isfinite(value)) or (not floating and value.bit_length() > 64):
            self.error()
        return value

    def value(self, depth: int = 0, scalar: Callable[[str], None] | None = None,
              *, tree: bool = True, route: int = 0, event: Callable | None = None,
              relay_context: tuple | list = (), relay_sanitize: Callable | None = None,
              selected: Callable | None = None, selected_path: tuple = (),
              submission_identity: tuple | None = None) -> Any:
        self.check()
        if depth > 64:
            raise _EvidenceCapacity("JSON depth capacity refused")
        self.whitespace()
        if self.index >= self.end:
            self.error()
        self.ledger.charge(0, 1, self.check)
        char = self.text[self.index]
        if selected is not None:
            self.ledger.charge(8 * len(selected_path), len(selected_path), self.check)
            if char in '[{':
                selected(selected_path, 'object' if char == '{' else 'array', None)
        deferred = selected is not None and _EvidenceObservations.encoded(selected_path)
        if route == 2 and char != "[" or route == 3 and char != "{":
            raise ValueError("Relay entry shape withheld")
        if char == '"':
            value = self.string()
            try:
                if scalar is not None and not deferred:
                    scalar(value)
                if selected is not None:
                    selected(selected_path, 'scalar', value)
                if event is not None:
                    event("scalar", value)
                return value
            except BaseException:
                value = None
                raise
        if char in "-0123456789":
            value = self.number()
            if selected is not None:
                selected(selected_path, 'scalar', value)
            if event is not None:
                event("scalar", value)
            return value
        for spelling, primitive in (("true", True), ("false", False), ("null", None)):
            if self.text.startswith(spelling, self.index, self.end):
                self.index += len(spelling)
                if selected is not None:
                    selected(selected_path, 'scalar', primitive)
                if event is not None:
                    event("scalar", primitive)
                return primitive
        if char not in "[{":
            self.error()
        self.ledger.charge(8, 1, self.check)
        # Active original-key set only in scan mode; arrays have no retained
        # members. The same recursion is the charged depth-bounded syntax stack.
        output = ({} if tree else set()) if char == "{" else ([] if tree else None)
        proof = _EvidenceRelay(relay_context, self.ledger, self.check, relay_sanitize, depth) if route == 3 else None
        selection = None
        close = "}" if char == "{" else "]"
        if event is not None:
            event("object" if char == "{" else "array", None)
        self.index += 1
        self.whitespace()
        member_index = 0
        try:
            if self.index < self.end and self.text[self.index] == close:
                self.index += 1
                if event is not None:
                    event("end-object" if char == "{" else "end-array", None)
                if selected is not None:
                    selected(selected_path, 'end-object' if char == '{' else 'end-array', None)
                if selection is not None:
                    self.relay_captured = selection.close() or self.relay_captured
                if proof is not None:
                    self.relay_captured = proof.close() or self.relay_captured
                return output if tree else None
            while True:
                if char == "{":
                    key = self.string()
                    if key in output:
                        self.error()  # Decoded escaped-equal keys, before their value.
                    if scalar is not None:
                        scalar(key)
                    if selected is not None:
                        selected(selected_path, 'key', key)
                    if route == 1 and key in ('starts', 'durable', 'provider_commands', 'provider_attempts') and selection is None:
                        selection = _EvidenceObservations(relay_context, self.ledger, self.check, relay_sanitize, depth, submission_identity)
                    if event is not None:
                        event("key", key)
                    self.whitespace()
                    if self.index >= self.end or self.text[self.index] != ":":
                        self.error()
                    self.index += 1
                    self.ledger.charge(16, 1, self.check)
                    if not tree:
                        output.add(key)  # Same decoded key, no additional string copy.
                    if proof is not None and key == "response_text":
                        self.whitespace()
                        if self.index >= self.end or self.text[self.index] != '"':
                            raise ValueError("Relay response text withheld")
                    child = self.value(depth + 1, None if proof is not None and key == "response_text" else scalar,
                        tree=tree, route=2 if route == 1 and key == "relay" else 0,
                        event=(proof.response_event if key == "response" else proof.request_event
                               if key == "request" else None) if proof is not None else event,
                        relay_context=relay_context, relay_sanitize=relay_sanitize,
                        selected=selection.event if route == 1 and key in ('starts', 'durable', 'provider_commands', 'provider_attempts') else selected,
                        selected_path=(key,) if route == 1 else selected_path + (key,) if selected is not None else (),
                        submission_identity=submission_identity)
                    if proof is not None:
                        proof.field(key, child)
                    if tree:
                        output[key] = child
                    child = None
                else:
                    self.ledger.charge(8, 1, self.check)
                    child = self.value(depth + 1, scalar, tree=tree, route=3 if route == 2 else 0,
                                       event=event, relay_context=relay_context, relay_sanitize=relay_sanitize,
                                       selected=selected, selected_path=selected_path + (member_index,) if selected is not None else (),
                                       submission_identity=submission_identity)
                    member_index += 1
                    if tree:
                        output.append(child)
                    child = None
                self.whitespace()
                if self.index >= self.end:
                    self.error()
                delimiter = self.text[self.index]
                self.index += 1
                if delimiter == close:
                    if event is not None:
                        event("end-object" if char == "{" else "end-array", None)
                    if selected is not None:
                        selected(selected_path, 'end-object' if char == '{' else 'end-array', None)
                    if selection is not None:
                        self.relay_captured = selection.close() or self.relay_captured
                    if proof is not None:
                        self.relay_captured = proof.close() or self.relay_captured
                    return output if tree else None
                if delimiter != ",":
                    self.error()
                self.whitespace()
        except BaseException:
            if output is not None:
                output.clear()
            raise
        finally:
            if selection is not None:
                selection.release()
            if proof is not None:
                proof.release()
            if not tree:
                if output is not None:
                    output.clear()
            key = None

    def read(self, *, top: str | None = None, scalar: Callable[[str], None] | None = None,
             jsonl: bool = False, observations: bool = False, context: tuple | list = (),
             sanitize: Callable | None = None, submission_identity: tuple | None = None) -> Any:
        if top is not None:
            return self.scan(top, scalar, jsonl=jsonl, tree=True, observations=observations,
                             context=context, sanitize=sanitize, submission_identity=submission_identity)
        result = self.value()
        self.whitespace()
        if self.index != self.end:
            self.error()
        self.check()
        return result

    def scan(self, top: str, scalar: Callable[[str], None] | None, *, jsonl: bool = False,
             tree: bool = False, observations: bool = False, context: tuple | list = (),
             sanitize: Callable | None = None, depth: int = 0, event: Callable | None = None,
             submission_identity: tuple | None = None) -> Any:
        """Validate the SAME image; JSONL bounds are indices, never line copies."""
        records = result = None
        try:
            if tree and jsonl:
                self.ledger.charge(8, 1, self.check)
                records = []
            if jsonl and not self.text:
                self.error()  # Empty evidence is never a launch witness.
            while True:
                if jsonl:
                    self.end = self.text.find("\n", self.index)
                    if self.end < 0:
                        self.error()  # Even a valid unfinished last object refuses.
                self.whitespace()
                if self.index >= self.end or self.text[self.index] != top:
                    self.error()
                self.ledger.charge(8, 1, self.check)  # Record's root syntax frame, before entry.
                result = self.value(depth, scalar=scalar, tree=tree, route=1 if observations else 0,
                                    event=event, relay_context=context, relay_sanitize=sanitize, submission_identity=submission_identity)
                self.whitespace()
                if self.index != self.end:
                    self.error()
                self.check()
                if not jsonl:
                    break
                if tree:
                    self.ledger.charge(8, 1, self.check)
                    records.append(result)
                self.index = self.end + 1  # Preserve the original full LF image.
                if self.index == len(self.text):
                    break
            if self.relay_captured:
                _evidence_original(self.text, context, self.ledger, self.check, sanitize)
            else:
                # All decoded scalars have closed. Also check the SAME complete
                # original, including punctuation and every JSONL record/LF join.
                # No context is required by the unrelated syntax-only callers.
                for secret in context:
                    self.ledger.charge(8, 1, self.check)
                    if secret in self.text:
                        raise ValueError("original evidence content withheld")
            self.check()
            return records if jsonl else result
        except BaseException:
            if records is not None:
                records.clear()
            if type(result) in (dict, list):
                result.clear()
            raise
        finally:
            result = records = None
            self.text = ""  # Refused-image traceback keeps syntax facts, not input.


def redact(text: str, token: str, context: list[str]) -> str:
    """Use only the complete original literal redaction context."""
    if not token or not context or token not in context:
        raise ValueError("redaction context unavailable")
    text = text.replace(token, "[REDACTED]")
    for secret in context:
        text = text.replace(secret, "[REDACTED]")
    return text


def _ordinary_evidence_error(error: BaseException) -> bool:
    # Subclasses precede OSError/ValueError. Safety-state failures never call
    # this predicate: a category alone cannot authorize continuation.
    if isinstance(error, (TimeoutError, InterruptedError, KeyboardInterrupt, SystemExit, GeneratorExit)):
        return False
    return isinstance(error, (UnicodeError, OSError, ValueError))


def _collection_annotation(state: dict, reason: str) -> tuple:
    return state.get("operation", "UNKNOWN"), reason, state.get("chars", "U"), state.get("utf8_bytes", "U")


def _collection_line(phase: str, window: str, kind: str, annotation: tuple) -> bytes | None:
    windows = ("ROOT", "TOKEN_READ", "TOKEN_EMPTY", "CONTEXT_PRESENT", "CONTEXT_READ", "CONTEXT_DECODE",
               "CONTEXT_VALIDATE", "SCENARIO_PRESENT", "SCENARIO_READ", "SCENARIO_DECODE", "SCENARIO_MERGE",
               "RAW_ENUM", "TEXT_READ", "REDACT", "IMAGE_READ", "REQUIRED", "UNKNOWN")
    operations = (set(windows) - {"SCENARIO_MERGE"}) | {"SCENARIO_OPEN", "SCENARIO_UTF8", "SCENARIO_CAPACITY", "SCENARIO_PROJECT", "TEXT_UTF8"}
    reasons = {"IO", "UNICODE", "VALUE", "CAPACITY", "DECODE", "MISSING", "UNSAFE", "DEADLINE", "INTERRUPTION", "OTHER", "UNKNOWN"}
    operation, reason, chars, size = annotation
    if (phase not in ("cleanup", "export") or window not in windows or kind not in ("F", "U")
            or operation not in operations or reason not in reasons
            or any(value != "U" and (type(value) is not int or not 0 <= value <= cap)
                   for value, cap in ((chars, 8388609), (size, 33554436)))):
        return None
    return f"HRD1:C:{phase.upper()}:{window}:{kind}:D1:{operation}:{reason}:{chars}:{size}\n".encode("ascii")


def collect_evidence(root: Path | None, root_identity: tuple | None, receipt: dict, exercise: str,
                     staged: dict[str, bytes], state: dict, check: Callable[[], None],
                     failed: Callable[..., None]) -> tuple[bool, tuple | None]:
    """Scenario first, then the SAME raw enumeration after ordinary refusal.

    State retains complete original context/snapshots, refused sources and a fatal latch.
    Failed operations reserve their real cause before diagnostics/continuation.
    """
    first_error = None
    incomplete = False
    ledger = _EvidenceLedger()  # SAME original collection opportunity, through raw siblings.
    state.update(operation="ROOT", window="ROOT", chars="U", utf8_bytes="U")

    def refuse(error: BaseException | None, *, reason: str | None = None) -> None:
        nonlocal first_error, incomplete
        incomplete = True
        if error is not None and first_error is None:
            first_error = (error, error.__traceback__)
        category = _failure_error(error) if error is not None else "other"
        if reason is None:
            reason = {"timeout": "DEADLINE", "interruption": "INTERRUPTION", "unicode": "UNICODE",
                      "io": "IO", "value": "VALUE"}.get(category, "OTHER")
        failed(error, category=category, annotation=_collection_annotation(state, reason))

    def mark(operation: str) -> None:
        state["operation"] = operation
        state["window"] = {"SCENARIO_OPEN": "SCENARIO_READ", "SCENARIO_UTF8": "SCENARIO_READ",
                           "SCENARIO_CAPACITY": "SCENARIO_READ", "SCENARIO_PROJECT": "SCENARIO_MERGE",
                           "TEXT_UTF8": "REDACT"}.get(operation, operation)

    try:
        if state.get("fatal"):
            raise ValueError("detailed evidence stopped")
        check()
        if root is None or root_identity is None:
            raise ValueError("owned evidence root unavailable")
        with _failure_directories(root, check, coord=False) as (directory, verify):
            if tuple(root_identity) != (os.fstat(directory).st_dev, os.fstat(directory).st_ino):
                raise ValueError("owned evidence root identity changed")

            def eligible() -> None:
                try:
                    check()
                    verify()
                    if state.get("fatal"):
                        raise ValueError("detailed evidence stopped")
                    check()
                except BaseException:
                    state["fatal"] = True
                    raise

            def step(operation: str, action: Callable[[], Any]) -> Any:
                mark(operation)
                eligible()
                value = action()
                eligible()
                return value

            # Partial/moved context can never replace the original trusted list.
            candidate_token = state.get("token", "")
            if not candidate_token:
                candidate_token = step("TOKEN_READ", lambda: (root / "daemon/daemon.token").read_text().strip())
            mark("TOKEN_EMPTY")
            if not candidate_token:
                raise ValueError("redaction token unavailable")
            context_path = root / "coord/redaction-context.json"
            if not step("CONTEXT_PRESENT", context_path.exists):
                raise ValueError("retained redaction context unavailable")
            def read_context() -> str:
                with context_path.open(encoding="utf8", newline="") as stream:
                    return _evidence_input(stream, ledger, eligible, lambda text: None, lambda size: None)

            context_text = step("CONTEXT_READ", read_context)
            context = step("CONTEXT_DECODE", lambda: _EvidenceJSON(context_text, ledger, eligible).read())
            del context_text
            mark("CONTEXT_VALIDATE")
            if (type(context) is not list or not context or any(type(s) is not str or not s for s in context)
                    or candidate_token not in context or state.get("context") and context != state["context"]
                    or any("[REDACTED]" in secret or secret in "[REDACTED]" for secret in context)):
                raise ValueError("invalid or changed retained redaction context")
            eligible()
            state.update(token=candidate_token, context=context)
            ledger.charge(8, 1, eligible)
            refused_sources = state.setdefault("refused_sources", set())

            def remember_refusal(name: str) -> None:
                if name not in refused_sources:
                    refused_sources.add(name)  # Slot charged before its acquisition.

            # A derived view, charged once under this SAME opportunity. Original
            # list/order still owns identity comparisons across acquisitions.
            ledger.charge(sum(_evidence_utf8_size(s, eligible) for s in context) + 8 * len(context),
                          len(context), eligible)
            ordered = tuple(sorted(context, key=lambda s: (-len(s), s)))

            def sanitize(text: str) -> str:
                try:
                    for secret in ordered:
                        eligible()
                        text = text.replace(secret, "[REDACTED]")
                    return text
                except BaseException:
                    state["fatal"] = True
                    raise

            scenario_present = False
            scenario_slot = False
            try:
                ledger.charge(32, 1, eligible)
                scenario_slot = True
                scenario_path = root / "scenario.json"
                scenario_present = step("SCENARIO_PRESENT", scenario_path.exists)
                if not scenario_present:
                    refuse(None, reason="MISSING")
                else:
                    if "scenario.json" in refused_sources:
                        refuse(None, reason="VALUE")  # No new cause or content attempt.
                    else:
                        scenario_file = step("SCENARIO_OPEN", lambda: scenario_path.open(encoding="utf8", newline=""))
                        with scenario_file:
                            def returned(text: str) -> None:
                                state["chars"] = len(text)
                                mark("SCENARIO_UTF8")

                            def measured(size: int) -> None:
                                state["utf8_bytes"] = size
                                mark("SCENARIO_CAPACITY")

                            mark("SCENARIO_READ")
                            scenario_text = _evidence_input(scenario_file, ledger, eligible, returned, measured)
                        result = step("SCENARIO_DECODE", lambda: _EvidenceJSON(scenario_text, ledger, eligible).read())
                        detached = step("SCENARIO_PROJECT", lambda: scenario_fields(
                            result, sanitize, context=ordered, ledger=ledger, check=eligible))
                        del scenario_text, result
                        state["safe_scenario"] = detached  # Complete assignment only.
                        receipt.update(detached)
            except BaseException as exc:
                fatal = state.get("fatal") or isinstance(exc, _EvidenceUnsafe) or not _ordinary_evidence_error(exc)
                if fatal:
                    state["fatal"] = True
                reason = ("CAPACITY" if isinstance(exc, _EvidenceCapacity)
                          else "DECODE" if state["operation"] == "SCENARIO_DECODE" and isinstance(exc, json.JSONDecodeError)
                          else None)
                refuse(exc, reason=reason)
                if fatal:
                    raise
                if scenario_slot:
                    remember_refusal("scenario.json")
            eligible()  # Slow ordinary errors cannot borrow another allowance.
            paths = iter(step("RAW_ENUM", lambda: (root / "raw").glob("*")))
            while True:
                try:
                    path = step("RAW_ENUM", lambda: next(paths))
                except StopIteration:
                    break
                except BaseException as exc:
                    fatal = not _ordinary_evidence_error(exc)
                    if fatal:
                        state["fatal"] = True
                    refuse(exc)
                    if fatal:
                        raise
                    eligible()
                    break  # No re-enumeration after unavailable membership.
                try:
                    ledger.charge(_evidence_utf8_size(path.name, eligible) + 16, 1, eligible)
                except _EvidenceCapacity as exc:
                    refuse(exc, reason="CAPACITY")
                    break
                try:
                    if path.name in refused_sources:
                        refuse(None, reason="VALUE")
                        continue
                    if path.suffix in {".txt", ".json", ".jsonl"}:
                        def read_raw() -> str:
                            with path.open(encoding="utf8", errors="strict", newline="") as stream:
                                return _evidence_input(stream, ledger, eligible, lambda text: None, lambda size: None)
                        text = step("TEXT_READ", read_raw)
                        mark("REDACT")
                        eligible()
                        if path.name in {"observations.json", "e07.json", "e07-prefix.json",
                                         "browser-events.json", "e05-browser-events.json", "launch-witnesses.jsonl"}:
                            ledger.charge(32, 4, eligible)  # Scanner/callback/bounds before allocation.
                            def raw_scalar(value: str) -> None:
                                eligible()
                                if "\\" in value:
                                    raise ValueError("raw decoded representation withheld")
                                # Required replacement is an ordinary WHOLE-member
                                # refusal, before the independent promised safe pass.
                                for secret in ordered:
                                    ledger.charge(8, 1, eligible)
                                    if secret in value:
                                        raise ValueError("raw decoded content withheld")
                                size = _evidence_utf8_size(value, eligible)
                                safe = _evidence_safe(value, size, sanitize, ordered, ledger, eligible)
                                ledger.charge(8, 1, eligible)
                                if safe != value:
                                    raise _EvidenceUnsafe("raw unchanged postcondition")

                            top = "[" if path.name in {"browser-events.json", "e05-browser-events.json"} else "{"
                            _EvidenceJSON(text, ledger, eligible).scan(
                                top, raw_scalar, jsonl=path.name == "launch-witnesses.jsonl",
                                observations=path.name in {"observations.json", "e07-prefix.json"},
                                context=ordered, sanitize=sanitize, submission_identity=(str(root), receipt.get("source_sha")))
                            safe = text  # ONE original image, only after full closure.
                        else:
                            if "\\" in text:
                                raise ValueError("raw text representation withheld")
                            size = _evidence_utf8_size(text, eligible)
                            safe = _evidence_safe(text, size, sanitize, ordered, ledger, eligible)
                            if "\\" in safe or any(secret in safe for secret in state["context"]):
                                state["fatal"] = True
                                raise ValueError("unsafe raw sanitation")
                        eligible()
                        size = _evidence_utf8_size(safe, eligible)
                        # Literal emission preserves original whitespace/escapes/LFs.
                        # Charge span facts before allocation; the buffer precharges
                        # its complete bytes and bounded scratch before encoding.
                        ledger.charge(48, 5, eligible)
                        content = step("TEXT_UTF8", lambda: _evidence_buffer(
                            [("literal", [(safe, 0, len(safe))])], size, ledger, eligible))
                        staged[path.name] = content
                    elif path.suffix == ".png":
                        staged[path.name] = step("IMAGE_READ", path.read_bytes)
                except BaseException as exc:
                    fatal = state.get("fatal") or isinstance(exc, _EvidenceUnsafe) or not _ordinary_evidence_error(exc)
                    if fatal:
                        state["fatal"] = True
                    refuse(exc, reason="UNSAFE" if fatal and _ordinary_evidence_error(exc)
                           else "CAPACITY" if isinstance(exc, _EvidenceCapacity) else None)
                    if fatal:
                        raise
                    remember_refusal(path.name)
                finally:
                    text = safe = content = None
                eligible()
            mark("REQUIRED")
            eligible()
            required = (BASELINE_ARTIFACTS + [phase["name"] + ".txt" for phase in receipt["phases"]]
                        if exercise == "baseline" else ["observations.json"])
            if not scenario_present or any(name not in staged or (name.endswith((".json", ".png")) and not staged[name]) for name in required):
                refuse(None, reason="MISSING")
            eligible()
            if not incomplete:
                mark("UNKNOWN")
            return not incomplete, first_error
    except BaseException as exc:
        state["fatal"] = True
        # Preserve an earlier actual exception; do not manufacture a replacement
        # first claim when an optional diagnostic/eligibility check also failed.
        if first_error is None or first_error[0] is not exc:
            refuse(exc, reason="UNSAFE" if _ordinary_evidence_error(exc) else None)
        raise


def _publication_destination(output: Path, output_identity: tuple, name: str,
                             check: Callable[[], None]) -> None:
    with _failure_directories(output, check, coord=False, owned=False) as (directory, verify):
        verify()
        info = os.fstat(directory)
        if (info.st_dev, info.st_ino) != output_identity or Path(name).name != name:
            raise ValueError("evidence destination changed")
        for leaf in (name, "result.tmp" if name == "result.json" else name + ".tmp"):
            check()
            try:
                info = os.stat(leaf, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode) or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)) or info.st_nlink > 1 and stat.S_ISREG(info.st_mode):
                raise ValueError("unsafe evidence destination")
        verify()
        check()


def publish_evidence(output: Path, output_identity: tuple, staged: dict[str, bytes], published: dict[str, bytes],
                     failed_members: set[str], state: dict, check: Callable[[], None],
                     failed: Callable[[BaseException], None]) -> None:
    """Drain safe bytes only; failed members have no second-pass retry."""
    if state.get("fatal"):
        raise ValueError("detailed evidence stopped")
    for name, content in staged.items():
        if name in failed_members or published.get(name) == content:
            continue
        try:
            # Safety failures are fatal, independently of their exception class.
            try:
                check()
                _publication_destination(output, output_identity, name, check)
            except BaseException:
                state["fatal"] = True
                raise
            temporary = output / (name + ".tmp")
            with temporary.open("xb") as stream:
                stream.write(content)
            check()
            try:
                _publication_destination(output, output_identity, name, check)
            except BaseException:
                state["fatal"] = True
                raise
            temporary.replace(output / name)
            published[name] = content  # Actual replace, before any later check.
            check()
        except BaseException as exc:
            failed_members.add(name)
            if not _ordinary_evidence_error(exc):
                state["fatal"] = True
            failed(exc)
            if state.get("fatal"):
                raise
            check()


def _result_json(row: dict, check: Callable[[], None], *, token: str = "",
                 context: list[str] | None = None) -> bytearray:
    """Encode the selected closed FAIL or original-context detailed envelope.

    None denotes only the existing closed-field contract, never raw detail.
    Explicit unavailable/invalid detailed context cannot select that contract.
    """
    ledger = _EvidenceLedger()  # This original result attempt, never collection work.
    ledger.charge(32, 4, check)
    ordered = ()
    if context is not None:
        if (type(token) is not str or not token or type(context) is not list or not context
                or any(type(secret) is not str or not secret for secret in context)
                or token not in context
                or any("[REDACTED]" in secret or secret in "[REDACTED]" for secret in context)):
            raise _EvidenceUnsafe("result original context unavailable")
        size = sum(_evidence_utf8_size(secret, check) for secret in context)
        # Original references, ordering keys/sort scratch and frozen view, before
        # allocation. No context acquisition, transformed dictionary or reset.
        ledger.charge(size * 2 + 40 * len(context), 4 * len(context), check)
        ordered = tuple(sorted(context, key=lambda secret: (-len(secret), secret)))
    return _evidence_json(row, ordered, ledger, check)


def publish_failure(output: Path, output_identity: tuple, row: dict, check: Callable[[], None]) -> None:
    """The original nonfinal FAIL opportunity; no postcommit success inference."""
    _publication_destination(output, output_identity, "result.json", check)
    candidate = _result_json(row, check)
    temporary = output / "result.tmp"
    with temporary.open("wb") as stream:
        stream.write(candidate)
    _publication_destination(output, output_identity, "result.json", check)
    temporary.replace(output / "result.json")


def failure_receipt(receipt: dict, safe_scenario: dict, categories: dict, completed_exported: dict | None,
                    ownership_evidence: dict | None, exercise: str, started: float) -> dict:
    """Only fully detached decoded-safe claims accompany closed FAIL metadata."""
    closure = receipt["cleanup"]
    safe_cleanup = {key: closure[key] for key in
                    ("identities", "groups", "ports_absent", "data_absent", "sockets_absent", "watcher_stopped")
                    if key in closure}
    safe_cleanup.update(ok=False, observed_units=closure.get("observed_units", sorted(closure.get("units", {}))),
                        observed_groups=closure.get("observed_groups", sorted(closure.get("groups", {}))))
    binding = {key: receipt[key] for key in ADMISSION_FIELDS + ("interpreter", "node") if key in receipt}
    safe_timing = dict(receipt["timing"], cleanup=dict(receipt["timing"]["cleanup"], ok=False))
    return dict(binding, **categories, schema=1, status="FAIL", source_sha=receipt["source_sha"],
                python=receipt["python"], exercise=exercise, skipped=0,
                **safe_scenario, signals=receipt["signals"], phases=receipt["phases"],
                cleanup=safe_cleanup, timing=safe_timing,
                failure_phase=receipt.get("failure_phase", "export"), failure="incomplete detailed evidence",
                completed_cases=completed_exported, total_seconds=time.monotonic() - started,
                **({"ownership_evidence": ownership_evidence} if ownership_evidence is not None else {}))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", required=True, help="absolute supported interpreter")
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--exercise", choices=["baseline", "cancellation"], default="baseline")
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    output = args.artifacts.resolve()
    output.mkdir(parents=True, exist_ok=False)
    output_info = output.stat()
    output_identity = (output_info.st_dev, output_info.st_ino)
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
    retained_completed = None
    completed_staged: dict[str, bytes] = {}
    completed_exported = None

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
        env.update(E2E_SOURCE_TREE=receipt["source_tree"], E2E_RUN_ID=receipt["run_id"],
                   E2E_RUN_ATTEMPT=receipt["run_attempt"], E2E_PYTHON=receipt["python"])
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
                        process = owner.popen(launch_argv, cwd=cwd, env=env, stdout=stream,
                                                   stderr=subprocess.STDOUT, **launch_options)
                    finally:
                        if diagnostic is not None:
                            diagnostic.release_endpoint("sender")  # Before wait, also on Popen failure.
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
        # Only the existing collector admits detached scenario claims.
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
        collection_state = dict(token="", context=[], safe_scenario=dict(variants={}), fatal=False,
                                operation="UNKNOWN", window="UNKNOWN", chars="U", utf8_bytes="U")
        collection_diagnostic: dict[str, tuple] = {}
        published_members: dict[str, bytes] = {}
        failed_members: set[str] = set()

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

        def incomplete(name: str, error: BaseException | None = None, *, category: str = "other",
                       annotation: tuple = ("UNKNOWN", "UNKNOWN", "U", "U")) -> tuple | None:
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
                claim = (receipt["timing"]["phase"], collection_state["window"], "F" if error is not None else "U", object(), annotation)
                if collection_diagnostic.setdefault("claim", claim) is claim:
                    return claim
            return None

        def observe_collection(claim: tuple | None) -> None:
            if claim is None or collection_diagnostic.get("claim") is not claim or "output" in collection_diagnostic:
                return
            collection_diagnostic["output"] = claim  # Charge before eligibility/write.
            phase, window, kind, _, annotation = claim
            line = _collection_line(phase, window, kind, annotation)
            if line is None:
                return
            end = receipt["timing"][phase]["deadline"] - (1 if phase == "export" else 0)
            if time.monotonic() >= end:
                return
            _diagnostic_write(line, end)

        def collection_failed(error: BaseException | None, *, category: str, annotation: tuple) -> None:
            if error is not None and receipt["timing"]["phase"] == "export":
                export_failure.setdefault("first", (error, error.__traceback__))
            claim = incomplete("collection", error, category=category, annotation=annotation)
            observe_collection(claim)

        def stage_evidence(check: Callable[[], None]) -> None:
            nonlocal detail_complete
            complete, error = collect_evidence(root, root_identity, receipt, args.exercise,
                                               staged, collection_state, check, collection_failed)
            if complete:
                detail_complete = True
            if error is not None:
                raise error[0].with_traceback(error[1])
            check()

        try:
            arm_deadline(cleanup["deadline"])
            # Closed incremental evidence is independent of final raw-log
            # redaction/export and must be retained before root teardown.
            if root is not None:
                try:
                    retained_completed, completed_staged = completed.read_bundle(root / "coord", receipt)
                except FileNotFoundError:
                    pass  # No accepted ledger; cases remain UNKNOWN.
                except (OSError, ValueError, KeyError, TypeError, UnicodeError) as exc:
                    claim = incomplete("collection", exc)
                    observe_collection(claim)
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
                if owner and not owner.thread.is_alive():
                    receipt["cleanup"].setdefault("identities", owner.pids.copy())
                    receipt["cleanup"].setdefault("observed_units", sorted(owner.units.copy()))
                    receipt["cleanup"].setdefault("observed_groups", sorted(owner.groups.copy()))

        export = reserve("export", 120, hard_end)
        final_encoding_refused = False

        def publish(row: dict, *, final: bool = False) -> None:
            nonlocal final_result_committed, final_encoding_refused
            temporary = output / "result.tmp"
            destination = output / "result.json"
            if not final:
                def failure_time() -> None:
                    if time.monotonic() >= export["deadline"]:
                        raise TimeoutError("export deadline exhausted")
                publish_failure(output, output_identity, row, failure_time)
                return
            _publication_destination(output, output_identity, "result.json", export_time)
            try:
                candidate = _result_json(row, export_time, token=collection_state["token"],
                                         context=collection_state["context"])
            except BaseException:
                final_encoding_refused = True
                raise
            with temporary.open("wb") as stream:
                stream.write(candidate)
            _publication_destination(output, output_identity, "result.json", export_time)
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

        def failure_row() -> dict:
            return failure_receipt(receipt, collection_state["safe_scenario"], failure_categories(),
                                   completed_exported, ownership_evidence, args.exercise, started)

        initial_fail_published = False

        def export_time() -> None:
            if time.monotonic() >= export["deadline"] - 1:
                raise TimeoutError("export deadline exhausted")

        def export_failed(exc: BaseException) -> None:
            nonlocal export_incomplete
            export_failure.setdefault("first", (exc, exc.__traceback__))
            export_incomplete = True
            latch_launcher("export", exc)

        def ordinary_export(action: Callable[[], None]) -> None:
            try:
                action()
            except BaseException as exc:
                export_failed(exc)
                if collection_state["fatal"] or not _ordinary_evidence_error(exc):
                    collection_state["fatal"] = True
                    raise
                export_time()

        try:
            arm_deadline(export["deadline"])
            if retained_completed is not None:
                # Independent closed references precede any result naming them.
                def export_completed() -> None:
                    nonlocal completed_exported
                    for name, data in completed_staged.items():
                        export_time()
                        completed.publish(output, name, data)
                    export_time()
                    copied, _ = completed.read_bundle(output, receipt)
                    if copied != retained_completed:
                        raise ValueError("completed export identity moved")
                    completed_exported = copied
                ordinary_export(export_completed)
            try:
                publish(failure_row())
                initial_fail_published = True  # Only actual replacement counts.
            except BaseException as exc:
                export_failed(exc)
                if not _ordinary_evidence_error(exc):
                    collection_state["fatal"] = True
                    raise
                # Validate destination independently; unsafe state is fatal.
                try:
                    _publication_destination(output, output_identity, "result.json", export_time)
                except BaseException:
                    collection_state["fatal"] = True
                    raise
            arm_deadline(export["deadline"] - 1)
            publish_evidence(output, output_identity, staged, published_members, failed_members,
                             collection_state, export_time, export_failed)
            if not collection_state["fatal"] and root and root.exists():
                ordinary_export(lambda: stage_evidence(export_time))
            # The second ORIGINAL pass also drains newly safe bytes after an
            # ordinary recollection refusal. It never retries failed members.
            publish_evidence(output, output_identity, staged, published_members, failed_members,
                             collection_state, export_time, export_failed)
            export_time()
            if export_failure:
                error, tb = export_failure["first"]
                raise error.with_traceback(tb)
            if not detail_complete or staging_incomplete:
                raise ValueError("incomplete detailed evidence")
            if not collection_state["token"]:
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
                receipt["completed_cases"] = completed_exported
                completed.join_results(completed_exported, receipt)
            receipt.update(failure_categories())
            receipt["completed_cases"] = completed_exported
            export_time()
            # Only the collector's complete detached claims enter this envelope.
            # Metadata shares the bounded typed original-context admission; no
            # raw serialization/reparse or traceback fallback can rescue it.
            receipt.update(collection_state["safe_scenario"])
            if ownership_evidence is not None:
                receipt["ownership_evidence"] = ownership_evidence
            publish(receipt, final=True)
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
                    # A refused new envelope cannot rewrite an ENTIRE earlier
                    # FAIL. Retain the original closed fallback when initial
                    # persistence was unavailable, and for other export errors.
                    if not (initial_fail_published and final_encoding_refused):
                        publish(failure_row())
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
