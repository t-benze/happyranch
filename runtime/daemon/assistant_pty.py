from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
import errno
import fcntl
import os
from pathlib import Path
import pty
import secrets
import select
import signal
import shutil
import shlex
import subprocess
import struct
import sys
import tempfile
import termios
import time

from runtime.config import Settings


PROBE_REQUEST = "HAPPYRANCH_ASSISTANT_PTY_PROBE_REQUEST"
PROBE_READY = "HAPPYRANCH_ASSISTANT_PTY_PROBE_READY"

_OUTPUT_EXCERPT_BYTES = 4096
_READY_EXIT_OBSERVATION_SECONDS = 0.25
_SESSION_REPLAY_CHARS = 8192
_SESSION_SUBSCRIBER_QUEUE_SIZE = 256
_PTY_EXEC_HELPER_MODULE = "runtime.daemon.pty_exec_helper"


@dataclass(frozen=True)
class InteractiveExecutorSpec:
    name: str
    argv: list[str]
    prompt_surface: str
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ProbeResult:
    passed: bool
    executor: str
    output_excerpt: str
    detail: str
    elapsed_seconds: float
    timed_out: bool = False
    error: str | None = None
    returncode: int | None = None


def _close_fd(fd: int) -> None:
    try:
        os.close(fd)
    except OSError:
        return


def _process_group_exists(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _signal_process_tree(pid: int, sig: signal.Signals) -> None:
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            return
    except OSError:
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            return


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    pid = process.pid
    _signal_process_tree(pid, signal.SIGTERM)
    if _wait_for_process_group_exit(process, pid, timeout_seconds=0.5):
        return
    _signal_process_tree(pid, signal.SIGKILL)
    _wait_for_process_group_exit(process, pid, timeout_seconds=0.5)


def _wait_for_process_group_exit(
    process: subprocess.Popen[bytes],
    pid: int,
    *,
    timeout_seconds: float,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=0)
        if not _process_group_exists(pid):
            return True
        time.sleep(0.01)
    if process.poll() is not None:
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=0)
        if not _process_group_exists(pid):
            return True
    return False


def _write_all(fd: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        offset += os.write(fd, data[offset:])


def _set_pty_window_size(fd: int, *, rows: int, cols: int) -> None:
    if rows <= 0 or cols <= 0:
        raise ValueError("terminal size must be positive")
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _parse_selected_command(command: str) -> list[str]:
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise ValueError(f"assistant command is invalid: {exc}") from exc
    if not argv:
        raise ValueError("assistant command is empty")
    return argv


def _build_session_launch_argv(
    *,
    executable: str,
    argv: list[str],
    slave_fd: int,
    cwd: Path,
) -> list[str]:
    return [
        sys.executable,
        "-m",
        _PTY_EXEC_HELPER_MODULE,
        "--slave-fd",
        str(slave_fd),
        "--cwd",
        str(cwd),
        "--",
        executable,
        *argv[1:],
    ]


class AssistantPtySession:
    def __init__(
        self,
        *,
        command: str,
        workspace: Path,
        argv: list[str] | None = None,
    ) -> None:
        self.command = command
        self.workspace = workspace
        self.argv = list(argv) if argv is not None else _parse_selected_command(command)
        if not self.argv:
            raise ValueError("assistant command is empty")
        self.master_fd: int | None = None
        self.process: subprocess.Popen[bytes] | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._subscribers: set[asyncio.Queue[str | None]] = set()
        self._replay: list[str] = []
        self._replay_chars = 0
        self._terminal_sent = False
        self._closing = False
        self._closed = False

    async def start(self) -> None:
        if self.master_fd is not None and self.process is not None:
            return
        env = os.environ.copy()
        executable = shutil.which(self.argv[0], path=env.get("PATH"))
        if executable is None:
            raise FileNotFoundError(f"executable not found: {self.argv[0]}")
        master_fd, slave_fd = pty.openpty()
        launch_argv = _build_session_launch_argv(
            executable=executable,
            argv=self.argv,
            slave_fd=slave_fd,
            cwd=self.workspace,
        )
        try:
            process = subprocess.Popen(
                launch_argv,
                env=env,
                start_new_session=True,
                close_fds=True,
                pass_fds=(slave_fd,),
            )
        except BaseException:
            _close_fd(master_fd)
            raise
        finally:
            _close_fd(slave_fd)
        self.process = process
        self.master_fd = master_fd
        self._closed = False
        self._closing = False
        self._terminal_sent = False
        self._reader_task = asyncio.create_task(self._reader_loop())

    def matches(
        self,
        *,
        command: str,
        workspace: Path,
        argv: list[str] | None = None,
    ) -> bool:
        return (
            self.command == command
            and self.workspace == workspace
            and (argv is None or self.argv == argv)
        )

    def is_running(self) -> bool:
        if self._closed or self.process is None:
            return False
        return self.process.poll() is None

    def subscribe(self) -> asyncio.Queue[str | None]:
        queue: asyncio.Queue[str | None] = asyncio.Queue(
            maxsize=len(self._replay) + _SESSION_SUBSCRIBER_QUEUE_SIZE
        )
        for text in self._replay:
            queue.put_nowait(text)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[str | None]) -> None:
        self._subscribers.discard(queue)

    async def write_text(self, text: str) -> None:
        if self.master_fd is None or self._closed:
            raise RuntimeError("assistant session is closed")
        data = text.encode()
        await asyncio.to_thread(_write_all, self.master_fd, data)

    async def resize(self, *, rows: int, cols: int) -> None:
        if self.master_fd is None or self._closed:
            raise RuntimeError("assistant session is closed")
        await asyncio.to_thread(
            _set_pty_window_size,
            self.master_fd,
            rows=rows,
            cols=cols,
        )

    async def close(self) -> None:
        if self._closed and self.master_fd is None and self.process is None:
            return
        self._closing = True
        self._closed = True
        if (
            self._reader_task is not None
            and self._reader_task is not asyncio.current_task()
            and not self._reader_task.done()
        ):
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
        process = self.process
        if process is not None:
            await asyncio.to_thread(_terminate_process, process)
            self.process = None
        self._close_master_fd()
        self._broadcast_terminal_once()
        self._subscribers.clear()

    async def _reader_loop(self) -> None:
        assert self.master_fd is not None
        try:
            while not self._closed:
                chunk = await asyncio.to_thread(self._read_once, self.master_fd)
                if chunk is None:
                    break
                if chunk:
                    self._broadcast(chunk.decode(errors="replace"))
        finally:
            if not self._closing:
                await self._handle_natural_exit()

    def _read_once(self, master_fd: int) -> bytes | None:
        try:
            readable, _, _ = select.select([master_fd], [], [], 0.1)
        except OSError as exc:
            if exc.errno == errno.EINTR:
                return b""
            return None
        if not readable:
            return b""
        try:
            chunk = os.read(master_fd, 1024)
        except OSError as exc:
            if exc.errno == errno.EIO:
                return None
            raise
        if not chunk:
            return None
        return chunk

    def _broadcast(self, text: str | None) -> None:
        if text is not None:
            self._replay.append(text)
            self._replay_chars += len(text)
            while self._replay_chars > _SESSION_REPLAY_CHARS and self._replay:
                removed = self._replay.pop(0)
                self._replay_chars -= len(removed)
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(text)
            except asyncio.QueueFull:
                self._close_slow_subscriber(queue)

    def _close_slow_subscriber(self, queue: asyncio.Queue[str | None]) -> None:
        self._subscribers.discard(queue)
        while True:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        queue.put_nowait(None)

    async def _handle_natural_exit(self) -> None:
        self._closed = True
        self._close_master_fd()
        process = self.process
        if process is not None:
            await asyncio.to_thread(process.wait)
            self.process = None
        self._broadcast_terminal_once()

    def _close_master_fd(self) -> None:
        master_fd = self.master_fd
        if master_fd is not None:
            _close_fd(master_fd)
            self.master_fd = None

    def _broadcast_terminal_once(self) -> None:
        if self._terminal_sent:
            return
        self._terminal_sent = True
        self._broadcast(None)


class AssistantSessionManager:
    def __init__(self) -> None:
        self._session: AssistantPtySession | None = None
        self._lock = asyncio.Lock()

    async def get_or_start(
        self,
        *,
        command: str,
        workspace: Path,
        argv: list[str] | None = None,
    ) -> AssistantPtySession:
        async with self._lock:
            if (
                self._session is not None
                and self._session.matches(command=command, workspace=workspace, argv=argv)
                and self._session.is_running()
            ):
                return self._session
            if self._session is not None:
                await self._session.close()
            session = AssistantPtySession(command=command, workspace=workspace, argv=argv)
            await session.start()
            self._session = session
            return session

    async def close_all(self) -> None:
        async with self._lock:
            if self._session is not None:
                await self._session.close()
                self._session = None


def build_probe_request(nonce: str) -> str:
    return f"{PROBE_REQUEST} {nonce}"


def build_probe_response(nonce: str) -> str:
    return f"{PROBE_READY} {nonce}"


def build_executor_specs(settings: Settings) -> list[InteractiveExecutorSpec]:
    return [
        InteractiveExecutorSpec(
            name="claude",
            argv=[settings.claude_cli_path],
            prompt_surface="CLAUDE.md",
        ),
        InteractiveExecutorSpec(
            name="codex",
            argv=[settings.codex_cli_path],
            prompt_surface="AGENTS.md",
        ),
        InteractiveExecutorSpec(
            name="opencode",
            argv=[settings.opencode_cli_path],
            prompt_surface="AGENTS.md",
        ),
        InteractiveExecutorSpec(
            name="pi",
            argv=[settings.pi_cli_path],
            prompt_surface="AGENTS.md",
        ),
    ]


class ProbeRunner:
    def probe_executor(
        self,
        spec: InteractiveExecutorSpec,
        *,
        timeout_seconds: float = 3,
    ) -> ProbeResult:
        start = time.monotonic()
        nonce = self._new_probe_nonce()
        probe_request = build_probe_request(nonce)
        expected_response = build_probe_response(nonce)
        with tempfile.TemporaryDirectory(prefix="happyranch-assistant-probe-") as tmp:
            workspace = Path(tmp)
            self._write_prompt_surface(workspace, spec.prompt_surface)
            return self._probe_in_workspace(
                spec,
                workspace=workspace,
                timeout_seconds=timeout_seconds,
                start=start,
                probe_request=probe_request,
                expected_response=expected_response,
            )

    def _probe_in_workspace(
        self,
        spec: InteractiveExecutorSpec,
        *,
        workspace: Path,
        timeout_seconds: float,
        start: float,
        probe_request: str,
        expected_response: str,
    ) -> ProbeResult:
        master_fd: int | None = None
        child_pid: int | None = None
        exec_ready_fd: int | None = None
        exec_signal_fd: int | None = None
        returncode: int | None = None
        output = bytearray()
        try:
            env = os.environ.copy()
            env.update(spec.env)
            if not spec.argv:
                return self._result(
                    False,
                    spec,
                    output,
                    start,
                    "executor argv is empty",
                    error="launch_error",
                )
            executable = shutil.which(spec.argv[0], path=env.get("PATH"))
            if executable is None:
                return self._result(
                    False,
                    spec,
                    output,
                    start,
                    f"executable not found: {spec.argv[0]}",
                    error="launch_error",
                )
            exec_ready_fd, exec_signal_fd = os.pipe()
            os.set_inheritable(exec_signal_fd, False)
            child_pid, master_fd = pty.fork()
            if child_pid == 0:
                self._close_fd(exec_ready_fd)
                self._exec_child(spec.argv, executable, workspace, env, exec_signal_fd)
            self._close_fd(exec_signal_fd)
            exec_signal_fd = None
            exec_started, exec_error = self._wait_for_child_exec(
                exec_ready_fd,
                deadline=start + timeout_seconds,
            )
            if not exec_started:
                returncode = self._wait_for_returncode(
                    child_pid,
                    deadline=time.monotonic() + 0.1,
                )
                if exec_error is not None:
                    return self._result(
                        False,
                        spec,
                        output,
                        start,
                        exec_error,
                        error="launch_error",
                        returncode=returncode if returncode is not None else 127,
                    )
                return self._result(
                    False,
                    spec,
                    output,
                    start,
                    "timed out waiting for executor to start",
                    timed_out=True,
                    error="timeout",
                    returncode=self._poll_returncode(child_pid),
                )
            self._close_fd(exec_ready_fd)
            exec_ready_fd = None
            response_start = len(output)
            self._write_probe_request(master_fd, probe_request)
            deadline = start + timeout_seconds
            while time.monotonic() < deadline:
                self._read_available(master_fd, output, deadline)
                if self._has_ready_response(
                    output,
                    start_index=response_start,
                    expected_response=expected_response,
                ):
                    returncode = self._observe_ready_returncode(
                        child_pid,
                        master_fd,
                        output,
                        deadline,
                    )
                    if returncode is not None:
                        return self._result(
                            False,
                            spec,
                            output,
                            start,
                            f"ready marker observed but executor exited {returncode}",
                            error=(
                                "not_interactive"
                                if returncode == 0
                                else "nonzero_exit"
                            ),
                            returncode=returncode,
                        )
                    return self._result(
                        True,
                        spec,
                        output,
                        start,
                        "ready marker observed",
                        returncode=returncode,
                    )
                returncode = self._poll_returncode(child_pid)
                if returncode is not None:
                    self._drain_available(master_fd, output, time.monotonic() + 0.1)
                    break
            timed_out = returncode is None and time.monotonic() >= deadline
            detail = (
                "timed out waiting for ready marker"
                if timed_out
                else "expected ready marker not found"
            )
            return self._result(
                False,
                spec,
                output,
                start,
                detail,
                timed_out=timed_out,
                error="timeout" if timed_out else None,
                returncode=returncode,
            )
        except OSError as exc:
            error = "launch_error" if child_pid is None else "pty_error"
            return self._result(
                False,
                spec,
                output,
                start,
                str(exc),
                error=error,
                returncode=returncode,
            )
        finally:
            if child_pid is not None:
                self._terminate_process(child_pid)
            if exec_signal_fd is not None:
                self._close_fd(exec_signal_fd)
            if exec_ready_fd is not None:
                self._close_fd(exec_ready_fd)
            if master_fd is not None:
                self._close_fd(master_fd)

    def _exec_child(
        self,
        argv: list[str],
        executable: str,
        workspace: Path,
        env: dict[str, str],
        exec_error_fd: int,
    ) -> None:
        try:
            os.chdir(workspace)
            os.execvpe(executable, argv, env)
        except OSError as exc:
            message = f"failed to exec {argv[0]}: {exc}"
            try:
                os.write(exec_error_fd, message.encode())
            except OSError:
                pass
            os.write(2, f"{message}\n".encode())
        os._exit(127)

    def _write_prompt_surface(
        self,
        workspace: Path,
        prompt_surface: str,
    ) -> None:
        (workspace / prompt_surface).write_text(
            "\n".join(
                [
                    "# HappyRanch Assistant PTY Probe",
                    "",
                    "This temporary workspace is used only for readiness probing.",
                    (
                        f"When the user sends `{PROBE_REQUEST} <token>`, reply with "
                        f"`{PROBE_READY} <the same token>`."
                    ),
                    "Do not include any other text in the reply.",
                    "",
                ]
            )
        )

    def _write_probe_request(self, master_fd: int, probe_request: str) -> None:
        self._write_all(master_fd, f"{probe_request}\r".encode())

    def _write_all(self, fd: int, data: bytes) -> None:
        offset = 0
        while offset < len(data):
            offset += os.write(fd, data[offset:])

    def _wait_for_child_exec(
        self,
        fd: int,
        *,
        deadline: float,
    ) -> tuple[bool, str | None]:
        while time.monotonic() < deadline:
            timeout = max(0.0, min(0.05, deadline - time.monotonic()))
            readable, _, _ = select.select([fd], [], [], timeout)
            if not readable:
                continue
            chunk = os.read(fd, 4096)
            if chunk == b"":
                return True, None
            return False, chunk.decode(errors="replace")
        return False, None

    def _has_ready_response(
        self,
        output: bytearray,
        *,
        start_index: int,
        expected_response: str,
    ) -> bool:
        text = bytes(output[start_index:]).decode(errors="replace")
        return any(line.strip() == expected_response for line in text.splitlines())

    def _observe_ready_returncode(
        self,
        child_pid: int,
        master_fd: int,
        output: bytearray,
        deadline: float,
    ) -> int | None:
        observation_deadline = min(
            deadline,
            time.monotonic() + _READY_EXIT_OBSERVATION_SECONDS,
        )
        returncode = self._poll_returncode(child_pid)
        while returncode is None and time.monotonic() < observation_deadline:
            self._read_available(master_fd, output, observation_deadline)
            returncode = self._poll_returncode(child_pid)
        return returncode

    def _new_probe_nonce(self) -> str:
        return secrets.token_hex(16)

    def _read_available(
        self,
        master_fd: int,
        output: bytearray,
        deadline: float,
    ) -> bool:
        timeout = max(0.0, min(0.05, deadline - time.monotonic()))
        try:
            readable, _, _ = select.select([master_fd], [], [], timeout)
        except OSError as exc:
            if exc.errno == errno.EINTR:
                return False
            raise
        if not readable:
            return False
        try:
            chunk = os.read(master_fd, 1024)
        except OSError as exc:
            if exc.errno == errno.EIO:
                return False
            raise
        if chunk:
            output.extend(chunk)
            if len(output) > _OUTPUT_EXCERPT_BYTES:
                del output[:-_OUTPUT_EXCERPT_BYTES]
            return True
        return False

    def _drain_available(
        self,
        master_fd: int,
        output: bytearray,
        deadline: float,
    ) -> None:
        while time.monotonic() < deadline:
            if not self._read_available(master_fd, output, deadline):
                return

    def _result(
        self,
        passed: bool,
        spec: InteractiveExecutorSpec,
        output: bytearray,
        start: float,
        detail: str,
        *,
        timed_out: bool = False,
        error: str | None = None,
        returncode: int | None = None,
    ) -> ProbeResult:
        return ProbeResult(
            passed=passed,
            executor=spec.name,
            output_excerpt=bytes(output).decode(errors="replace"),
            detail=detail,
            elapsed_seconds=time.monotonic() - start,
            timed_out=timed_out,
            error=error,
            returncode=returncode,
        )

    def _poll_returncode(self, pid: int) -> int | None:
        try:
            waited_pid, status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return 0
        if waited_pid == 0:
            return None
        return self._status_to_returncode(status)

    def _wait_for_returncode(self, pid: int, *, deadline: float) -> int | None:
        returncode = self._poll_returncode(pid)
        while returncode is None and time.monotonic() < deadline:
            time.sleep(0.01)
            returncode = self._poll_returncode(pid)
        return returncode

    def _terminate_process(self, pid: int) -> None:
        self._signal_process_tree(pid, signal.SIGTERM)
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            self._poll_returncode(pid)
            if not self._process_group_exists(pid):
                return
            time.sleep(0.01)
        self._signal_process_tree(pid, signal.SIGKILL)
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            self._poll_returncode(pid)
            if not self._process_group_exists(pid):
                return
            time.sleep(0.01)

    def _process_group_exists(self, pid: int) -> bool:
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            return True
        return True

    def _signal_process_tree(self, pid: int, sig: signal.Signals) -> None:
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                return
        except OSError:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                return

    def _status_to_returncode(self, status: int) -> int:
        if os.WIFEXITED(status):
            return os.WEXITSTATUS(status)
        if os.WIFSIGNALED(status):
            return -os.WTERMSIG(status)
        return status

    def _close_fd(self, fd: int) -> None:
        try:
            os.close(fd)
        except OSError:
            return
