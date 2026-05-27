"""Subprocess execution for script_requests (spec §6).

Owns one short-lived asyncio coroutine per run: spawn → pump stdout/stderr →
fan out events to in-memory subscribers via the `publish` callback → terminate.

Module-level state: a registry of in-flight `asyncio.subprocess.Process` objects
keyed by SR id, used by the daemon shutdown path to SIGTERM/SIGKILL on exit.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import signal
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


def migrate_filesystem_layout(org_root: Path | str) -> None:
    """Rename ``<org_root>/scripts/`` → ``<org_root>/jobs/`` and each ``SR-*``
    file to ``JOB-*``.

    Idempotent: a no-op if ``jobs/`` already exists OR if ``scripts/`` doesn't
    exist. Called once per org at daemon startup, before any runner uses the
    directory. Companion to the DB-side ``script_requests`` → ``jobs`` rename
    in ``Database._migrate_jobs_table_if_needed``: without this, every historic
    row's ``stdout_path``/``stderr_path`` (now pointing under ``jobs/``) would
    404.
    """
    org_root = Path(org_root)
    scripts_dir = org_root / "scripts"
    jobs_dir = org_root / "jobs"

    if jobs_dir.exists():
        return  # already migrated (or fresh install with the new layout)
    if not scripts_dir.exists():
        return  # nothing to migrate

    scripts_dir.rename(jobs_dir)
    for entry in jobs_dir.iterdir():
        if entry.name.startswith("SR-"):
            entry.rename(jobs_dir / ("JOB-" + entry.name[len("SR-"):]))


# In-flight registry; shutdown handler walks this to clean up.
_INFLIGHT: dict[str, asyncio.subprocess.Process] = {}

# Background runner tasks (the route's _run_and_persist coroutines). The shutdown
# hook awaits these AFTER killing subprocesses so they can transition the SR row
# to a terminal state before per-org DBs are closed. Without this wait, an
# in-flight SR stays `running` until the next daemon startup recovery scan.
_RUNNER_TASKS: dict[str, asyncio.Task] = {}

_HEAD_CAP_BYTES = 65536  # spec §3.1, §6.2


def register_runner_task(job_id: str, task: asyncio.Task) -> None:
    """Register a background _run_and_persist task so shutdown can await it."""
    _RUNNER_TASKS[job_id] = task
    task.add_done_callback(lambda _t: _RUNNER_TASKS.pop(job_id, None))


def _interpreter_binary(interpreter: str) -> str | None:
    return shutil.which(interpreter)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class JobRunResult:
    status: str           # "completed" | "failed"
    exit_code: int | None
    duration_ms: int
    stdout_head: str
    stderr_head: str
    stdout_bytes: int
    stderr_bytes: int
    truncated_stdout: bool
    truncated_stderr: bool
    reason: str | None = None   # populated only when status == "failed"


async def _pump_stream(
    stream: asyncio.StreamReader,
    label: str,
    out_path: str,
    publish: Callable[[dict], None],
    head_buf: list[bytes],
    head_capped: list[bool],
    byte_counter: list[int],
) -> None:
    """Append bytes to disk, fan out line events, fill head buffer until cap."""
    with open(out_path, "ab", buffering=0) as f:
        while True:
            line = await stream.readline()
            if not line:
                return
            f.write(line)
            byte_counter[0] += len(line)
            if not head_capped[0]:
                head_so_far = sum(len(b) for b in head_buf)
                room = _HEAD_CAP_BYTES - head_so_far
                if len(line) <= room:
                    head_buf.append(line)
                else:
                    if room > 0:
                        head_buf.append(line[:room])
                    head_capped[0] = True
            try:
                text = line.decode("utf-8", errors="replace").rstrip("\n")
            except Exception:
                text = "<binary>"
            publish({"kind": "line", "stream": label, "line": text, "ts": _now_iso()})


async def run_job(
    *,
    job_id: str | None = None,
    script_text: str,
    interpreter: str,
    cwd: str,
    stdout_path: str,
    stderr_path: str,
    timeout_seconds: int,
    publish: Callable[[dict], None],
) -> JobRunResult:
    """Spawn the script, pump streams, return JobRunResult.

    `publish` is called with each line event and the final terminal event.
    `job_id` is used only for the in-flight registry; pass None in unit tests.
    """
    binary = _interpreter_binary(interpreter)
    if binary is None:
        raise FileNotFoundError(f"interpreter unavailable: {interpreter}")

    started = datetime.now(timezone.utc)
    proc = await asyncio.create_subprocess_exec(
        binary,
        "-",  # read script from stdin (bash/sh/zsh/python3 all honor this)
        cwd=cwd,
        env=dict(os.environ),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    if job_id is not None:
        _INFLIGHT[job_id] = proc

    proc.stdin.write(script_text.encode("utf-8"))
    proc.stdin.close()

    stdout_head: list[bytes] = []
    stderr_head: list[bytes] = []
    stdout_capped: list[bool] = [False]
    stderr_capped: list[bool] = [False]
    stdout_bytes = [0]
    stderr_bytes = [0]

    pump_out = asyncio.create_task(_pump_stream(
        proc.stdout, "stdout", stdout_path, publish, stdout_head, stdout_capped, stdout_bytes
    ))
    pump_err = asyncio.create_task(_pump_stream(
        proc.stderr, "stderr", stderr_path, publish, stderr_head, stderr_capped, stderr_bytes
    ))

    reason: str | None = None
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout_seconds)
        await asyncio.gather(pump_out, pump_err)
        status = "completed"
        exit_code = proc.returncode
    except asyncio.TimeoutError:
        reason = "timeout"
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass
        # Drain pumps briefly; ignore errors.
        try:
            await asyncio.wait_for(asyncio.gather(pump_out, pump_err, return_exceptions=True), timeout=2)
        except asyncio.TimeoutError:
            pass
        status = "failed"
        exit_code = proc.returncode
    finally:
        if job_id is not None:
            _INFLIGHT.pop(job_id, None)

    finished = datetime.now(timezone.utc)
    duration_ms = int((finished - started).total_seconds() * 1000)

    head_marker_stdout = b"\n[truncated; see file]" if stdout_capped[0] else b""
    head_marker_stderr = b"\n[truncated; see file]" if stderr_capped[0] else b""
    stdout_head_str = b"".join(stdout_head).decode("utf-8", errors="replace") + head_marker_stdout.decode()
    stderr_head_str = b"".join(stderr_head).decode("utf-8", errors="replace") + head_marker_stderr.decode()

    result = JobRunResult(
        status=status,
        exit_code=exit_code,
        duration_ms=duration_ms,
        stdout_head=stdout_head_str,
        stderr_head=stderr_head_str,
        stdout_bytes=stdout_bytes[0],
        stderr_bytes=stderr_bytes[0],
        truncated_stdout=stdout_capped[0],
        truncated_stderr=stderr_capped[0],
        reason=reason,
    )

    publish({
        "kind": "terminal",
        "status": status,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "reason": reason,
        "ts": _now_iso(),
    })
    return result


def in_flight_job_ids() -> list[str]:
    return list(_INFLIGHT.keys())


async def terminate_all_inflight(
    *, grace_seconds: int = 5, persist_timeout_seconds: float = 5.0,
) -> None:
    """Daemon shutdown hook: SIGTERM every in-flight subprocess, then SIGKILL,
    then await the runner background tasks so they can persist terminal state
    (transition the SR row from `running` to `failed`/`completed`) BEFORE the
    caller closes per-org DB connections.

    Without the runner-task wait, the row sits in `running` until the next
    daemon startup's recovery scan — making a dead SR look live to founders
    in the meantime.
    """
    procs = list(_INFLIGHT.items())
    for job_id, proc in procs:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    if procs:
        await asyncio.sleep(grace_seconds)
        for job_id, proc in procs:
            if proc.returncode is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
    # Drain runner tasks so they persist terminal state before DBs close.
    # Snapshot once — entries self-clear via the done-callback registered by
    # register_runner_task.
    runners = list(_RUNNER_TASKS.values())
    if runners:
        try:
            await asyncio.wait_for(
                asyncio.gather(*runners, return_exceptions=True),
                timeout=persist_timeout_seconds,
            )
        except asyncio.TimeoutError:
            # Best effort — startup recovery scan handles any stragglers.
            pass
