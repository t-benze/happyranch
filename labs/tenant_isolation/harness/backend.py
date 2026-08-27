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
        self._daemons: dict[int, subprocess.Popen] = {}
        self._last_stop_daemon_ok: dict[int, bool] = {}

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

    def probe_node_http(
        self,
        proxy_host: str,
        proxy_port: int,
        target_host: str,
        target_port: int,
        timeout: float = 15.0,
    ) -> bool:
        """Genuine node-context data-plane probe.

        Opens a SOCKS5 CONNECT through the proxy of the SOURCE node (the node's
        own tailscaled userspace stack) and performs an HTTP GET to the target
        (a destination node's tailnet IP:connector port). True only when the
        connection through the node's own context succeeded with an HTTP 2xx.
        This is a real source-node-to-destination-node probe — never a
        runner-host connection to a control-plane port.
        """
        raise NotImplementedError

    def docker_inspect_state(self, name: str) -> str | None:
        """Return the container's state status ("running"/"exited"/...) or None
        when the container does not exist."""
        raise NotImplementedError

    def daemon_alive(self, pid: int) -> bool:
        """True when the daemon process is still alive (residue detection)."""
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

    def check_relay_tooling(self) -> tuple[bool, str]:
        """(available, reason) for the forced-relay direct-block tooling.

        The real relay proof needs to suppress direct (WireGuard/disco UDP)
        paths so the pinned tailscale client genuinely relays through the
        embedded headscale DERP. On the authorized isolated runner this is
        passwordless ``sudo iptables`` (a standard GitHub-hosted runner
        capability) — never production infrastructure. Unavailable => the
        harness stops with the exact prerequisite instead of weakening proof.
        """
        raise NotImplementedError

    def apply_relay_block(self, ports: list[int]) -> None:
        """Force relay: drop UDP egress from every node's fixed port so no
        direct path exists; only the real DERP relay (TCP) remains. Fail
        closed (raises) unless every rule is verifiably applied."""
        raise NotImplementedError

    def remove_relay_block(self, ports: list[int]) -> list[str]:
        """Remove the relay-block rules; return the list of ports whose rule
        removal FAILED (outcome-bearing; cleanup treats any non-empty as
        failure)."""
        raise NotImplementedError

    def relay_block_active(self, ports: list[int]) -> list[str]:
        """Return the ports whose relay-block rule is CURRENTLY ACTIVE.

        Used for residue detection: any active rule after cleanup is residue
        that fails the evidence closed."""
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


def normalize_expected_digest(expected: str) -> str:
    """Strip a ``sha256:`` prefix from a pinned digest (manifest convention).

    ``manifest.json`` pins digests as ``sha256:<hex>``; download verification
    compares against the raw hex of the downloaded bytes. Both forms are
    accepted so a prefixed manifest value is never a false mismatch.
    """
    return expected[7:] if expected.startswith("sha256:") else expected


def _node_ip_from_socket(cmd: list[str]) -> str:
    """Derive a stable fake 100.x tailnet IP from a ``--socket <path>`` arg.

    FakeBackend status responses need per-node identities so node-to-node
    probes see distinct targets; the socket path embeds the node id
    (``tailscaled-<cell><idx>.sock``).
    """
    import re

    joined = " ".join(str(c) for c in cmd)
    m = re.search(r"tailscaled-([ab])([123])\.sock", joined)
    if m:
        cell, idx = m.group(1), int(m.group(2))
        base = {"a": 0, "b": 3}[cell]
        return f"100.64.0.{base + idx}"
    return "100.64.0.1"


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

    def _socks5_connect(
        self, proxy_host: str, proxy_port: int, target_host: str, target_port: int, timeout: float
    ) -> tuple[socket.socket | None, bool]:
        """Minimal RFC-1928 SOCKS5 CONNECT (no-auth) to the target IPv4.

        Returns (socket, ok). The socket is returned so the caller can perform
        an HTTP request through the established node-context tunnel. The
        reply's BND.ADDR is consumed per ATYP so the stream is aligned.
        """
        import ipaddress

        try:
            ip = ipaddress.IPv4Address(target_host)
        except ValueError:
            return None, False
        try:
            sock = socket.create_connection((proxy_host, proxy_port), timeout=timeout)
            sock.settimeout(timeout)
            sock.sendall(b"\x05\x01\x00")
            if sock.recv(2) != b"\x05\x00":
                sock.close()
                return None, False
            sock.sendall(b"\x05\x01\x00\x01" + ip.packed + target_port.to_bytes(2, "big"))
            head = sock.recv(4)
            if len(head) != 4 or head[0] != 0x05 or head[1] != 0x00:
                sock.close()
                return None, False
            atyp = head[3]
            if atyp == 0x01:
                addr_len = 4
            elif atyp == 0x04:
                addr_len = 16
            elif atyp == 0x03:
                n = sock.recv(1)
                if not n:
                    sock.close()
                    return None, False
                addr_len = n[0]
            else:
                sock.close()
                return None, False
            addr = sock.recv(addr_len)
            if len(addr) != addr_len:
                sock.close()
                return None, False
            port = sock.recv(2)
            if len(port) != 2:
                sock.close()
                return None, False
            return sock, True
        except OSError:
            return None, False

    def probe_node_http(
        self,
        proxy_host: str,
        proxy_port: int,
        target_host: str,
        target_port: int,
        timeout: float = 15.0,
    ) -> bool:
        self._record(
            ["probe_node_http", str(proxy_host), str(proxy_port), str(target_host), str(target_port)],
            timeout,
        )
        sock, ok = self._socks5_connect(proxy_host, proxy_port, target_host, target_port, timeout)
        if not ok:
            return False
        try:
            sock.sendall(
                (
                    f"GET /health HTTP/1.0\r\nHost: {target_host}:{target_port}\r\n"
                    "Accept: */*\r\n\r\n"
                ).encode("ascii")
            )
            response = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
                if b"\r\n\r\n" in response:
                    break
            status_line = response.split(b"\r\n", 1)[0] if response else b""
            if status_line.startswith(b"HTTP/1.") and b" 2" in status_line[:12]:
                return True
            return False
        except OSError:
            return False
        finally:
            sock.close()

    def docker_inspect_state(self, name: str) -> str | None:
        result = self.run(
            ["docker", "inspect", "--format", "{{.State.Status}}", name], timeout=30.0
        )
        if not result.ok():
            return None
        return result.stdout.strip() or None

    def daemon_alive(self, pid: int) -> bool:
        import os

        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

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
        expected_hex = normalize_expected_digest(sha256)
        if digest != expected_hex:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(
                f"sha256 mismatch for {url}: expected {expected_hex}, got {digest} "
                "(fail-closed; pinned artifact bytes differ — see manifest.json)"
            )
        tmp.rename(dest)

    def start_daemon(
        self,
        cmd: list[str],
        log_path: Path,
        timeout: float = 30.0,
        env: dict[str, str] | None = None,
    ) -> int:
        import os

        self._record(["start_daemon"] + [str(c) for c in cmd], timeout)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log = open(log_path, "ab")
        full_env = dict(os.environ)
        if env:
            full_env.update(env)
        proc = subprocess.Popen(
            [str(c) for c in cmd],
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=full_env,
        )
        self._daemons[proc.pid] = proc
        self._daemon_pids.append(proc.pid)
        return proc.pid

    def check_relay_tooling(self) -> tuple[bool, str]:
        """Passwordless sudo + iptables on the isolated runner (standard
        GitHub-hosted runner capability). Anything less stops the run with the
        exact prerequisite; no relay claim is ever fabricated."""
        probe = self.run(["sudo", "-n", "iptables", "-L", "-n"], timeout=30.0)
        if not probe.ok():
            return False, (
                "sudo -n iptables unavailable: "
                f"{bounded_redacted_stderr(probe.stderr)}"
            )
        return True, ""

    def _iptables(self, args: list[str]) -> CmdResult:
        return self.run(["sudo", "iptables"] + args, timeout=30.0)

    def apply_relay_block(self, ports: list[int]) -> None:
        from .redact import bounded_redacted_stderr as _red

        for port in sorted(set(ports)):
            r = self._iptables(
                ["-I", "OUTPUT", "-p", "udp", "--sport", str(port), "-j", "DROP"]
            )
            if not r.ok():
                raise RuntimeError(
                    f"relay block apply failed for UDP port {port}: "
                    f"{_red(r.stderr)}"
                )
        active = self.relay_block_active(ports)
        if set(active) != set(str(p) for p in ports):
            raise RuntimeError(
                f"relay block not fully applied; active rules: {sorted(active)}"
            )

    def remove_relay_block(self, ports: list[int]) -> list[str]:
        failures: list[str] = []
        for port in sorted(set(ports)):
            r = self._iptables(
                ["-D", "OUTPUT", "-p", "udp", "--sport", str(port), "-j", "DROP"]
            )
            if not r.ok() and "Bad rule" not in r.stderr:
                failures.append(str(port))
        return failures

    def relay_block_active(self, ports: list[int]) -> list[str]:
        r = self._iptables(["-S", "OUTPUT"])
        if not r.ok():
            # cannot inspect => fail closed (treat every port as possibly active)
            return [str(p) for p in ports]
        present = {
            str(p) for p in ports if f"--sport {p}" in (r.stdout + r.stderr)
        }
        return [str(p) for p in ports if str(p) in present]


    def stop_daemon(self, pid: int) -> bool:
        """Terminate the daemon's process group, AWAIT termination, escalate to
        SIGKILL if needed. Returns True when the process is confirmed gone.

        The spawned child is REAPED (``Popen.wait``) so a zombie cannot keep
        its process group observable: without reaping, ``os.killpg(pid, 0)``
        keeps succeeding on a dead-but-unreaped child and every stop looks
        like a failure (and residue looks like a live process).
        """
        import os
        import signal

        self._record(["stop_daemon", str(pid)], 30.0)
        if pid <= 0:
            return False
        proc = self._daemons.get(pid)
        for signum in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(pid, signum)
            except ProcessLookupError:
                # already gone — reap to confirm
                self._reap_daemon(pid, proc)
                self._last_stop_daemon_ok[pid] = True
                return True
            except PermissionError:
                self._last_stop_daemon_ok[pid] = False
                return False
            # await actual termination (bounded); reaping keeps the group
            # observable — a zombie stays in its group until reaped.
            deadline = time.monotonic() + (10.0 if signum == signal.SIGTERM else 5.0)
            while time.monotonic() < deadline:
                if proc is not None:
                    try:
                        proc.wait(timeout=0.2)
                        self._reap_daemon(pid, proc)
                        self._last_stop_daemon_ok[pid] = True
                        return True
                    except subprocess.TimeoutExpired:
                        pass
                else:
                    try:
                        os.killpg(pid, 0)
                    except ProcessLookupError:
                        self._reap_daemon(pid, None)
                        self._last_stop_daemon_ok[pid] = True
                        return True
                time.sleep(0.2)
        # group still alive after SIGTERM + SIGKILL: escalation failed
        self._last_stop_daemon_ok[pid] = False
        return False

    def _reap_daemon(self, pid: int, proc: subprocess.Popen | None) -> None:
        if proc is not None:
            try:
                proc.wait(timeout=0.5)
            except Exception:
                pass
        self._daemons.pop(pid, None)
        self._daemon_pids = [p for p in self._daemon_pids if p != pid]

    def daemon_alive(self, pid: int) -> bool:
        """True when the daemon process is still alive (residue detection).

        Uses ``Popen.poll`` when the process is tracked so a reaped child can
        never be reported as live residue.
        """
        import os

        proc = self._daemons.get(pid)
        if proc is not None:
            return proc.poll() is None
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


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
        # node-context probe results: key = "<proxy_host> <proxy_port> <target_host> <target_port>"
        self.node_probe_results: dict[str, bool] = {}
        self.node_probe_default: bool = False  # deny-default (hostiles denied; positives need the real runtime)
        # container state map: name -> status or None (absent)
        self.container_states: dict[str, str | None] = {}
        self.docker_inspect_default: str | None = "running"
        # stop_daemon outcomes: pid -> bool (True = terminated cleanly)
        self.stop_daemon_outcomes: dict[int, bool] = {}
        self.stop_daemon_default: bool = True
        self.alive_pids: set[int] = set()
        # forced-relay direct-block state (iptables emulation)
        self.relay_block_applied: list[int] = []
        self.relay_block_available: bool = True
        self.relay_block_inspect_error: bool = False
        self.relay_block_remove_failures: list[int] = []

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
                    stdout='['
                    '{"given_name": "synth-a-client", "online": true, "last_seen": "2026-01-01T00:00:00Z", "forced_tags": ["tag:a-client"]},'
                    '{"given_name": "synth-a-client2", "online": true, "last_seen": "2026-01-01T00:00:00Z", "forced_tags": ["tag:a-client"]},'
                    '{"given_name": "synth-a-home", "online": true, "last_seen": "2026-01-01T00:00:00Z", "forced_tags": ["tag:a-home"]},'
                    '{"given_name": "synth-b-client", "online": true, "last_seen": "2026-01-01T00:00:00Z", "forced_tags": ["tag:b-client"]},'
                    '{"given_name": "synth-b-client2", "online": true, "last_seen": "2026-01-01T00:00:00Z", "forced_tags": ["tag:b-client"]},'
                    '{"given_name": "synth-b-home", "online": true, "last_seen": "2026-01-01T00:00:00Z", "forced_tags": ["tag:b-home"]}'
                    ']',
                )
            elif "status --json" in joined and "tailscale" in joined:
                # Per-node identity: derive a stable 100.x IP from the socket
                # path so node-to-node probes see distinct targets.
                ip = _node_ip_from_socket(cmd)
                result = CmdResult(
                    0,
                    stdout=(
                        '{"Self": {"HostName": "synth-node", "TailscaleIPs": ["%s"]}, "Peer": []}'
                        % ip
                    ),
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

    def probe_node_http(
        self,
        proxy_host: str,
        proxy_port: int,
        target_host: str,
        target_port: int,
        timeout: float = 15.0,
    ) -> bool:
        key = f"{proxy_host} {proxy_port} {target_host} {target_port}"
        self._record(["probe_node_http"] + key.split(), timeout)
        result = self._lookup(["probe_node_http"] + key.split())
        if result is not None:
            return result.returncode == 0
        if key in self.node_probe_results:
            return self.node_probe_results[key]
        return self.node_probe_default

    def docker_inspect_state(self, name: str) -> str | None:
        self._record(["docker_inspect_state", name], 30.0)
        result = self._lookup(["docker_inspect_state", name])
        if result is not None:
            return result.stdout.strip() or None if result.ok() else None
        if name in self.container_states:
            return self.container_states[name]
        return self.docker_inspect_default

    def daemon_alive(self, pid: int) -> bool:
        self._record(["daemon_alive", str(pid)], 30.0)
        return pid in self.alive_pids

    def check_runtime(self) -> tuple[bool, dict[str, str]]:
        self._record(["check_runtime"], 30.0)
        if self.docker_available:
            return True, {"docker": "fake 27.0"}
        return False, {}

    def download_verify(self, url: str, sha256: str, dest: Path) -> None:
        self._record(["download_verify", url], 300.0)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(f"fake artifact {sha256[:12]}", encoding="utf-8")

    def start_daemon(
        self,
        cmd: list[str],
        log_path: Path,
        timeout: float = 30.0,
        env: dict[str, str] | None = None,
    ) -> int:
        self._record(["start_daemon"] + [str(c) for c in cmd], timeout)
        # Emulate tailscaled creating its socket so the readiness predicate passes.
        if "--socket" in cmd:
            sock = Path(cmd[cmd.index("--socket") + 1])
            sock.parent.mkdir(parents=True, exist_ok=True)
            sock.touch()
        pid = 1000 + len(self.started_pids)
        self.started_pids.append(pid)
        self.alive_pids.add(pid)
        return pid

    def check_relay_tooling(self) -> tuple[bool, str]:
        self._record(["check_relay_tooling"], 30.0)
        if self.relay_block_available:
            return True, ""
        return False, "sudo -n iptables unavailable (fake)"

    def apply_relay_block(self, ports: list[int]) -> None:
        self._record(["apply_relay_block"] + [str(p) for p in ports], 30.0)
        if not self.relay_block_available:
            raise RuntimeError("relay block unavailable (no sudo/iptables on this host)")
        self.relay_block_applied = sorted(set(self.relay_block_applied) | set(ports))
        if self.relay_block_inspect_error:
            raise RuntimeError("relay block inspect failed (fail closed)")

    def remove_relay_block(self, ports: list[int]) -> list[str]:
        self._record(["remove_relay_block"] + [str(p) for p in ports], 30.0)
        failed = [p for p in ports if p in self.relay_block_remove_failures]
        self.relay_block_applied = [
            p for p in self.relay_block_applied if p not in ports or p in failed
        ]
        return [str(p) for p in failed]

    def relay_block_active(self, ports: list[int]) -> list[str]:
        self._record(["relay_block_active"], 30.0)
        if self.relay_block_inspect_error:
            return [str(p) for p in ports]
        return [str(p) for p in ports if p in self.relay_block_applied]

    def stop_daemon(self, pid: int) -> bool:
        self._record(["stop_daemon", str(pid)], 30.0)
        outcome = self.stop_daemon_outcomes.get(pid, self.stop_daemon_default)
        if outcome:
            self.started_pids = [p for p in self.started_pids if p != pid]
            self.alive_pids.discard(pid)
        return outcome
