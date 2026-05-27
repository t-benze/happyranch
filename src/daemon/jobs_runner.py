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


_READ_CHUNK_BYTES = 8192


async def _pump_stream(
    stream: asyncio.StreamReader,
    label: str,
    out_path: str,
    publish: Callable[[dict], None],
    head_buf: list[bytes],
    head_capped: list[bool],
    byte_counter: list[int],
    max_bytes: int | None = None,
    cap_event: asyncio.Event | None = None,
) -> None:
    """Append bytes to disk, fan out line events, fill head buffer until cap.

    Reads in chunks (not by line) so the byte counter advances regardless of
    whether the source process emits newlines — a server writing a 200 KB
    progress bar without flushing newlines must still trip the output cap.
    Line events are emitted by buffering chunks and splitting on `\\n`; a
    trailing partial line is flushed on EOF.

    When ``max_bytes`` is set and the running ``byte_counter`` for this stream
    crosses it, fire ``cap_event`` so the outer ``run_job`` coroutine can
    SIGKILL the process group. The pump keeps draining bytes already in
    flight — the on-disk file may exceed ``max_bytes`` by a small margin
    bounded by what's already in the OS pipe buffer at signal time.
    """
    line_buf = bytearray()
    with open(out_path, "ab", buffering=0) as f:
        while True:
            chunk = await stream.read(_READ_CHUNK_BYTES)
            if not chunk:
                # EOF — flush any trailing partial line as its own event.
                if line_buf:
                    _emit_line(bytes(line_buf), label, publish)
                    line_buf.clear()
                return
            f.write(chunk)
            byte_counter[0] += len(chunk)
            if (
                max_bytes is not None
                and cap_event is not None
                and byte_counter[0] >= max_bytes
                and not cap_event.is_set()
            ):
                cap_event.set()
                # Stop pumping immediately: backpressure the writer so the
                # OS pipe buffer fills, then SIGKILL (fired by the watcher)
                # closes the pipe before more bytes flow. Without this, on
                # platforms with large pipe buffers the writer can stream a
                # full payload through us between cap detection and SIGKILL.
                return
            if not head_capped[0]:
                head_so_far = sum(len(b) for b in head_buf)
                room = _HEAD_CAP_BYTES - head_so_far
                if len(chunk) <= room:
                    head_buf.append(chunk)
                else:
                    if room > 0:
                        head_buf.append(chunk[:room])
                    head_capped[0] = True
            # Split chunk on newlines to keep emitting line events for normal
            # well-behaved streams. Carry any partial trailing line forward.
            line_buf.extend(chunk)
            while True:
                nl = line_buf.find(b"\n")
                if nl < 0:
                    break
                line = bytes(line_buf[: nl + 1])
                del line_buf[: nl + 1]
                _emit_line(line, label, publish)


def _emit_line(line: bytes, label: str, publish: Callable[[dict], None]) -> None:
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
    max_runtime_seconds: int | None,
    publish: Callable[[dict], None],
    max_output_bytes: int | None = None,
) -> JobRunResult:
    """Spawn the script, pump streams, return JobRunResult.

    `publish` is called with each line event and the final terminal event.
    `job_id` is used only for the in-flight registry; pass None in unit tests.

    `max_runtime_seconds=None` means unbounded — the runner awaits the
    subprocess without an ``asyncio.wait_for`` wrapper. Positive int means
    the subprocess is SIGTERM'd (then SIGKILL'd) on expiry and the result's
    ``reason`` is set to ``"timeout"``.

    `max_output_bytes=None` means unbounded per stream. Positive int means
    either stream exceeding the threshold triggers an immediate SIGKILL of
    the process group; result is ``status="failed", reason="output_cap"``.
    Timeout precedence: if both could apply, ``"timeout"`` wins because the
    timeout branch sets ``reason`` first and the output_cap branch only
    overwrites a still-None reason.
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

    # Shared cap event — set by EITHER pump when its per-stream byte counter
    # crosses max_output_bytes. The watcher coroutine awaits it and SIGKILLs.
    cap_event = asyncio.Event()

    pump_out = asyncio.create_task(_pump_stream(
        proc.stdout, "stdout", stdout_path, publish, stdout_head, stdout_capped, stdout_bytes,
        max_bytes=max_output_bytes, cap_event=cap_event,
    ))
    pump_err = asyncio.create_task(_pump_stream(
        proc.stderr, "stderr", stderr_path, publish, stderr_head, stderr_capped, stderr_bytes,
        max_bytes=max_output_bytes, cap_event=cap_event,
    ))

    async def _watch_cap() -> None:
        await cap_event.wait()
        # Close the stdout/stderr read transports BEFORE SIGKILL. Two reasons:
        # 1. The writer gets SIGPIPE on its next write — stops pumping data
        #    into the pipe between our cap-detection and SIGKILL. Without
        #    this, on macOS the writer can flush its full payload (e.g. a
        #    200 KB single `write()`) through us during the kill race.
        # 2. asyncio's `proc.wait()` only resolves once the SubprocessTransport
        #    has torn down — which requires the pipe transports to close. If
        #    we leave bytes unread in stdout, the pipe stays open and
        #    `proc.wait()` hangs forever, even after the child is reaped.
        for fd in (1, 2):  # stdout, stderr
            try:
                t = proc._transport.get_pipe_transport(fd)
                if t is not None:
                    t.close()
            except Exception:
                pass
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    watcher = asyncio.create_task(_watch_cap())

    reason: str | None = None
    try:
        if max_runtime_seconds is None:
            await proc.wait()
        else:
            await asyncio.wait_for(proc.wait(), timeout=max_runtime_seconds)
        # After SIGKILL the OS closes the child's stdio pipes promptly, so
        # the gather() resolves quickly. The 5s safety wait_for guards
        # against pathological pumps blocked on disk.
        try:
            await asyncio.wait_for(
                asyncio.gather(pump_out, pump_err, return_exceptions=True),
                timeout=5,
            )
        except asyncio.TimeoutError:
            pass
        if cap_event.is_set():
            status = "failed"
            reason = "output_cap"
        else:
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
        watcher.cancel()
        try:
            await watcher
        except (asyncio.CancelledError, Exception):
            pass
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
