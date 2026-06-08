from __future__ import annotations

from dataclasses import dataclass, field
import errno
import os
from pathlib import Path
import pty
import select
import signal
import shutil
import tempfile
import time

from runtime.config import Settings


PROBE_REQUEST = "HAPPYRANCH_ASSISTANT_PTY_PROBE_REQUEST"
PROBE_READY = "HAPPYRANCH_ASSISTANT_PTY_PROBE_READY"

_OUTPUT_EXCERPT_BYTES = 4096


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
        with tempfile.TemporaryDirectory(prefix="happyranch-assistant-probe-") as tmp:
            workspace = Path(tmp)
            self._write_prompt_surface(workspace, spec.prompt_surface)
            return self._probe_in_workspace(
                spec,
                workspace=workspace,
                timeout_seconds=timeout_seconds,
                start=start,
            )

    def _probe_in_workspace(
        self,
        spec: InteractiveExecutorSpec,
        *,
        workspace: Path,
        timeout_seconds: float,
        start: float,
    ) -> ProbeResult:
        master_fd: int | None = None
        child_pid: int | None = None
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
            child_pid, master_fd = pty.fork()
            if child_pid == 0:
                self._exec_child(spec.argv, executable, workspace, env)
            response_start = len(output)
            self._write_probe_request(master_fd)
            deadline = start + timeout_seconds
            while time.monotonic() < deadline:
                self._read_available(master_fd, output, deadline)
                if self._has_ready_response(output, start_index=response_start):
                    returncode = self._poll_returncode(child_pid)
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
                    self._read_available(master_fd, output, time.monotonic())
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
            if child_pid is not None and returncode is None:
                self._terminate_process(child_pid)
            if master_fd is not None:
                self._close_fd(master_fd)

    def _exec_child(
        self,
        argv: list[str],
        executable: str,
        workspace: Path,
        env: dict[str, str],
    ) -> None:
        try:
            os.chdir(workspace)
            os.execvpe(executable, argv, env)
        except OSError as exc:
            os.write(2, f"failed to exec {argv[0]}: {exc}\n".encode())
        os._exit(127)

    def _write_prompt_surface(self, workspace: Path, prompt_surface: str) -> None:
        (workspace / prompt_surface).write_text(
            "\n".join(
                [
                    "# HappyRanch Assistant PTY Probe",
                    "",
                    "This temporary workspace is used only for readiness probing.",
                    (
                        f"When the user sends `{PROBE_REQUEST}`, reply with exactly "
                        f"`{PROBE_READY}`."
                    ),
                    "Do not include any other text in the reply.",
                    "",
                ]
            )
        )

    def _write_probe_request(self, master_fd: int) -> None:
        for char in f"{PROBE_REQUEST}\r":
            os.write(master_fd, char.encode())

    def _has_ready_response(self, output: bytearray, *, start_index: int) -> bool:
        text = bytes(output[start_index:]).decode(errors="replace")
        return any(line.strip() == PROBE_READY for line in text.splitlines())

    def _read_available(
        self,
        master_fd: int,
        output: bytearray,
        deadline: float,
    ) -> None:
        timeout = max(0.0, min(0.05, deadline - time.monotonic()))
        try:
            readable, _, _ = select.select([master_fd], [], [], timeout)
        except OSError as exc:
            if exc.errno == errno.EINTR:
                return
            raise
        if not readable:
            return
        try:
            chunk = os.read(master_fd, 1024)
        except OSError as exc:
            if exc.errno == errno.EIO:
                return
            raise
        if chunk:
            output.extend(chunk)
            if len(output) > _OUTPUT_EXCERPT_BYTES:
                del output[:-_OUTPUT_EXCERPT_BYTES]

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

    def _terminate_process(self, pid: int) -> None:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            if self._poll_returncode(pid) is not None:
                return
            time.sleep(0.01)
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            if self._poll_returncode(pid) is not None:
                return
            time.sleep(0.01)

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
