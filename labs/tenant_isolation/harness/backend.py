"""Backend abstraction for the hostile tenant-isolation lab harness.

Merge unit B (THR-097, TASK-5792). The orchestrator drives *all* runtime work
through a backend so its logic is unit-testable without Docker/headscale:
``DockerBackend`` executes the real lab on an isolated CI runner (GitHub
Actions ubuntu-latest — the repo's existing authorized CI runtime);
``FakeBackend`` scripts responses for deterministic unit tests and local
``--runtime mock`` dry-runs. A mock/unit-only pass is NOT proof of tenant
isolation and every run summary labels its ``runtime_kind`` honestly.
"""
from __future__ import annotations

import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


@dataclass
class CmdResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    command: list[str] | None = None

    def ok(self) -> bool:
        return self.returncode == 0


class Backend:
    """Base backend: records every invocation (command + timeout) for evidence."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.timeouts: list[float] = []
        self._daemon_pids: list[int] = []

    def _record(self, cmd: list[str], timeout: float) -> None:
        self.calls.append(list(cmd))
        self.timeouts.append(float(timeout))

    # -- primitives implemented by subclasses ---------------------------------

    def run(
        self,
        cmd: list[str],
        *,
        timeout: float = 30.0,
        cwd: Path | None = None,
        check: bool = False,
    ) -> CmdResult:
        raise NotImplementedError

    def docker_rm(self, name: str) -> CmdResult:
        raise NotImplementedError

    def docker_ps(self) -> list[str]:
        raise NotImplementedError

    def probe_tcp(self, host: str, port: int, timeout: float = 5.0) -> bool:
        raise NotImplementedError

    def check_runtime(self) -> tuple[bool, dict[str, str]]:
        """Return (available, {tool: version}) for the real runtime."""
        raise NotImplementedError

    def download_verify(self, url: str, sha256: str, dest: Path) -> None:
        raise NotImplementedError

    def start_daemon(self, cmd: list[str], log_path: Path, timeout: float = 30.0) -> int:
        """Start a long-running background process (e.g. tailscaled); return pid."""
        raise NotImplementedError

    def stop_daemon(self, pid: int) -> None:
        raise NotImplementedError

    # -- shared ----------------------------------------------------------------

    def wait_for(
        self,
        predicate: Callable[[], bool],
        timeout: float,
        interval: float = 1.0,
        desc: str = "condition",
    ) -> None:
        """Poll ``predicate`` until true or ``timeout`` expires (fail closed)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(interval)
        raise TimeoutError(f"timed out waiting for {desc} (>{timeout:.0f}s)")


class DockerBackend(Backend):
    """Real lab backend: docker CLI + pinned tailscale binary + TCP probes."""

    def run(
        self,
        cmd: list[str],
        *,
        timeout: float = 30.0,
        cwd: Path | None = None,
        check: bool = False,
    ) -> CmdResult:
        self._record(cmd, timeout)
        try:
            proc = subprocess.run(
                [str(c) for c in cmd],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(cwd) if cwd else None,
            )
        except FileNotFoundError:
            return CmdResult(
                returncode=127,
                stderr=f"command not found: {cmd[0]}",
                command=list(cmd),
            )
        except subprocess.TimeoutExpired as exc:
            return CmdResult(returncode=124, stderr=f"timed out after {timeout:.0f}s", command=list(cmd))
        result = CmdResult(
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            command=list(cmd),
        )
        if check and not result.ok():
            raise RuntimeError(
                f"command failed ({result.returncode}): {' '.join(cmd)}"
            )
        return result

    def docker_rm(self, name: str) -> CmdResult:
        return self.run(["docker", "rm", "-f", name], timeout=30.0)

    def docker_ps(self) -> list[str]:
        result = self.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}"], timeout=30.0
        )
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def probe_tcp(self, host: str, port: int, timeout: float = 5.0) -> bool:
        self._record(["probe_tcp", str(host), str(port)], timeout)
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    def check_runtime(self) -> tuple[bool, dict[str, str]]:
        self._record(["check_runtime"], 30.0)
        versions: dict[str, str] = {}
        docker = self.run(["docker", "--version"], timeout=30.0)
        if not docker.ok():
            return False, versions
        versions["docker"] = docker.stdout.strip() or docker.stderr.strip()
        return True, versions

    def download_verify(self, url: str, sha256: str, dest: Path) -> None:
        import hashlib

        self._record(["download_verify", url], 300.0)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        result = self.run(
            ["curl", "-fsSL", "--max-time", "240", "-o", str(tmp), url], timeout=300.0
        )
        if not result.ok():
            raise RuntimeError(f"download failed: {url}")
        digest = hashlib.sha256(tmp.read_bytes()).hexdigest()
        if digest != sha256:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(
                f"sha256 mismatch for {url}: expected {sha256}, got {digest} "
                "(fail-closed; pinned artifact bytes differ — see manifest.json)"
            )
        tmp.rename(dest)

    def start_daemon(self, cmd: list[str], log_path: Path, timeout: float = 30.0) -> int:
        import os

        self._record(["start_daemon"] + [str(c) for c in cmd], timeout)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log = open(log_path, "ab")
        proc = subprocess.Popen(
            [str(c) for c in cmd],
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self._daemon_pids.append(proc.pid)
        return proc.pid

    def stop_daemon(self, pid: int) -> None:
        import os
        import signal

        self._record(["stop_daemon", str(pid)], 30.0)
        try:
            os.killpg(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        self._daemon_pids = [p for p in self._daemon_pids if p != pid]


class FakeBackend(Backend):
    """Scripted backend for deterministic unit tests and ``--runtime mock``.

    ``script`` maps a command prefix (first 1-3 tokens joined by space) to a
    ``CmdResult``; unmatched commands succeed with empty output. ``docker_ps``
    returns ``leftover_containers`` so the residue check is testable.
    """

    def __init__(
        self,
        script: dict[str, CmdResult] | None = None,
        leftover_containers: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.script: dict[str, CmdResult] = script or {}
        self.leftover_containers: list[str] = leftover_containers or []
        self.docker_available = True
        self.started_pids: list[int] = []

    def _lookup(self, cmd: list[str]) -> CmdResult | None:
        for n in range(12, 0, -1):
            key = " ".join(str(c) for c in cmd[:n])
            if key in self.script:
                return self.script[key]
        return None

    def run(
        self,
        cmd: list[str],
        *,
        timeout: float = 30.0,
        cwd: Path | None = None,
        check: bool = False,
    ) -> CmdResult:
        self._record(cmd, timeout)
        result = self._lookup(cmd)
        if result is None:
            # Realistic defaults keep mock dry-runs flowing deterministically.
            joined = " ".join(str(c) for c in cmd)
            if "preauthkeys" in joined:
                result = CmdResult(0, stdout="mkey-mock-dryrunkey0000\n")
            elif "nodes list --output json" in joined:
                result = CmdResult(
                    0,
                    stdout='[{"given_name": "synth-a-client"},{"given_name": "synth-a-home"},{"given_name": "synth-b-client"},{"given_name": "synth-b-home"}]',
                )
            else:
                result = CmdResult(returncode=0, command=list(cmd))
        if check and not result.ok():
            raise RuntimeError(f"command failed ({result.returncode}): {' '.join(cmd)}")
        return result

    def docker_rm(self, name: str) -> CmdResult:
        return self.run(["docker_rm", name], timeout=30.0)

    def docker_ps(self) -> list[str]:
        self._record(["docker_ps"], 30.0)
        return list(self.leftover_containers)

    def probe_tcp(self, host: str, port: int, timeout: float = 5.0) -> bool:
        self._record(["probe_tcp", str(host), str(port)], timeout)
        result = self._lookup(["probe_tcp", str(host), str(port)])
        if result is not None:
            return result.returncode == 0
        return True

    def check_runtime(self) -> tuple[bool, dict[str, str]]:
        self._record(["check_runtime"], 30.0)
        if self.docker_available:
            return True, {"docker": "fake 27.0"}
        return False, {}

    def download_verify(self, url: str, sha256: str, dest: Path) -> None:
        self._record(["download_verify", url], 300.0)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(f"fake artifact {sha256[:12]}", encoding="utf-8")

    def start_daemon(self, cmd: list[str], log_path: Path, timeout: float = 30.0) -> int:
        self._record(["start_daemon"] + [str(c) for c in cmd], timeout)
        # Emulate tailscaled creating its socket so the readiness predicate passes.
        if "--socket" in cmd:
            sock = Path(cmd[cmd.index("--socket") + 1])
            sock.parent.mkdir(parents=True, exist_ok=True)
            sock.touch()
        pid = 1000 + len(self.started_pids)
        self.started_pids.append(pid)
        return pid

    def stop_daemon(self, pid: int) -> None:
        self._record(["stop_daemon", str(pid)], 30.0)
        self.started_pids = [p for p in self.started_pids if p != pid]
