from __future__ import annotations

from dataclasses import dataclass, field
import errno
import fcntl
import os
from pathlib import Path
import pty
import select
import signal
import subprocess
import tempfile
import termios
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
        slave_fd: int | None = None
        proc: subprocess.Popen[bytes] | None = None
        output = bytearray()
        try:
            master_fd, slave_fd = pty.openpty()
            env = os.environ.copy()
            env.update(spec.env)

            child_slave_fd = slave_fd

            def configure_child_pty() -> None:
                os.setsid()
                fcntl.ioctl(child_slave_fd, termios.TIOCSCTTY, 0)

            proc = subprocess.Popen(
                spec.argv,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=workspace,
                env=env,
                close_fds=True,
                preexec_fn=configure_child_pty,
            )
            os.close(slave_fd)
            slave_fd = None
            self._write_probe_request(master_fd)
            deadline = start + timeout_seconds
            while time.monotonic() < deadline:
                self._read_available(master_fd, output, deadline)
                if PROBE_READY.encode() in output:
                    return self._result(
                        True,
                        spec,
                        output,
                        start,
                        "ready marker observed",
                        returncode=proc.poll(),
                    )
                if proc.poll() is not None:
                    self._read_available(master_fd, output, time.monotonic())
                    break
            timed_out = proc.poll() is None and time.monotonic() >= deadline
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
                returncode=proc.poll(),
            )
        except OSError as exc:
            error = "launch_error" if proc is None else "pty_error"
            return self._result(
                False,
                spec,
                output,
                start,
                str(exc),
                error=error,
                returncode=proc.poll() if proc is not None else None,
            )
        finally:
            if proc is not None and proc.poll() is None:
                self._terminate_process(proc)
            if slave_fd is not None:
                self._close_fd(slave_fd)
            if master_fd is not None:
                self._close_fd(master_fd)

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

    def _terminate_process(self, proc: subprocess.Popen[bytes]) -> None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                return
            proc.wait(timeout=0.5)

    def _close_fd(self, fd: int) -> None:
        try:
            os.close(fd)
        except OSError:
            return
