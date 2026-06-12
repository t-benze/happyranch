from __future__ import annotations

import asyncio
import contextlib
import errno
import fcntl
import os
from pathlib import Path
import pty
import select
import signal
import shutil
import shlex
import subprocess
import struct
import sys
import termios
import time


_SESSION_REPLAY_CHARS = 8192
_SESSION_SUBSCRIBER_QUEUE_SIZE = 256
_DEFAULT_PTY_ROWS = 24
_DEFAULT_PTY_COLS = 80
_PTY_EXEC_HELPER_MODULE = "runtime.daemon.pty_exec_helper"


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
    # The system assistant launches its executor straight through this PTY and
    # never goes through CodexExecutor.run, so the localhost-network override
    # that org agents get must be re-applied here for codex. Codex's
    # `workspace-write` sandbox blocks all outbound sockets by default,
    # including localhost; without this override the assistant's `happyranch`
    # CLI calls die with `httpx.ConnectError: [Errno 1] Operation not permitted`
    # (the same TASK-080 class of failure CodexExecutor guards against —
    # see runtime/orchestrator/executors.py). `-c` is a global codex option
    # (`Usage: codex [OPTIONS] [PROMPT]`), placed immediately after the
    # executable. claude/opencode don't use Codex sandboxing, so they get
    # nothing.
    codex_network_override = (
        ["-c", "sandbox_workspace_write.network_access=true"]
        if os.path.basename(argv[0]) == "codex"
        else []
    )
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
        *codex_network_override,
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
        with contextlib.suppress(OSError, ValueError):
            _set_pty_window_size(
                master_fd,
                rows=_DEFAULT_PTY_ROWS,
                cols=_DEFAULT_PTY_COLS,
            )
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
