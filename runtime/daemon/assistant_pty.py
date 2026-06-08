from __future__ import annotations

from dataclasses import dataclass, field
import errno
import os
from pathlib import Path
import pty
import secrets
import select
import signal
import shutil
import tempfile
import time

from runtime.config import Settings


PROBE_REQUEST = "HAPPYRANCH_ASSISTANT_PTY_PROBE_REQUEST"
PROBE_READY = "HAPPYRANCH_ASSISTANT_PTY_PROBE_READY"

_OUTPUT_EXCERPT_BYTES = 4096
_READY_EXIT_OBSERVATION_SECONDS = 0.25


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
        passed = False
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
                self._exec_child(spec.argv, executable, workspace, env)
            self._close_fd(exec_signal_fd)
            exec_signal_fd = None
            if not self._wait_for_child_exec(exec_ready_fd, deadline=start + timeout_seconds):
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
                    if returncode is not None and returncode != 0:
                        return self._result(
                            False,
                            spec,
                            output,
                            start,
                            f"ready marker observed but executor exited {returncode}",
                            error="nonzero_exit",
                            returncode=returncode,
                        )
                    passed = True
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
            if child_pid is not None and not passed:
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
    ) -> None:
        try:
            os.chdir(workspace)
            os.execvpe(executable, argv, env)
        except OSError as exc:
            os.write(2, f"failed to exec {argv[0]}: {exc}\n".encode())
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

    def _wait_for_child_exec(self, fd: int, *, deadline: float) -> bool:
        while time.monotonic() < deadline:
            timeout = max(0.0, min(0.05, deadline - time.monotonic()))
            readable, _, _ = select.select([fd], [], [], timeout)
            if not readable:
                continue
            return os.read(fd, 1) == b""
        return False

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
        self._signal_process_tree(pid, signal.SIGTERM)
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            self._poll_returncode(pid)
            time.sleep(0.01)
        self._signal_process_tree(pid, signal.SIGKILL)
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            if self._poll_returncode(pid) is not None:
                return
            time.sleep(0.01)

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
