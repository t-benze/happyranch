"""Thin docker CLI wrappers with a full transcript of every command.

Every subprocess is recorded (argv, exit code, duration, stderr tail) into
the run transcript so the measured report can cite exact commands and exit
statuses. All names are run-id namespaced (see models). Host ports for the
metrics endpoint bind to 127.0.0.1 only; the control plane is never
published to the host.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

from cellspec import HEADSCALE_IMAGE, TAILSCALE_IMAGE
from cleanup import parse_docker_ps_json
from models import (
    client_container_name,
    headscale_container_name,
    network_name,
    volume_name,
)


class Transcript:
    def __init__(self, path: Path):
        self.path = path
        self.seq = 0

    def record(self, cmd: list[str], rc: int, dur_ms: int, stderr_tail: str) -> None:
        self.seq += 1
        entry = {
            "seq": self.seq,
            "cmd": cmd,
            "rc": rc,
            "dur_ms": dur_ms,
            "stderr_tail": stderr_tail[-200:],
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")


class Docker:
    def __init__(self, transcript: Transcript, run_id: str):
        self.transcript = transcript
        self.run_id = run_id

    def run(
        self,
        cmd: list[str],
        *,
        timeout: int = 120,
        check: bool = True,
        stdin_data: str | None = None,
    ) -> subprocess.CompletedProcess:
        start = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                input=stdin_data,
            )
            rc = proc.returncode
            err = (proc.stderr or "")[-200:]
        except subprocess.TimeoutExpired as exc:
            rc = -1
            err = f"TIMEOUT after {timeout}s"
            proc = subprocess.CompletedProcess(cmd, rc, "", err)
        dur = int((time.monotonic() - start) * 1000)
        self.transcript.record(cmd, rc, dur, err)
        if check and rc != 0:
            raise RuntimeError(f"command failed rc={rc}: {' '.join(cmd)}\nstderr: {err}")
        return proc

    # ── environment facts ─────────────────────────────────────────────
    def version_json(self) -> dict:
        out = self.run(["docker", "version", "--format", "{{json .}}"], check=False)
        try:
            return json.loads(out.stdout)
        except json.JSONDecodeError:
            return {}

    def image_digest(self, ref: str) -> str | None:
        out = self.run(
            ["docker", "inspect", "--format", "{{index .RepoDigests 0}}", ref],
            check=False,
        )
        for m in out.stdout.splitlines():
            if "sha256:" in m:
                return m.strip()
        return None

    # ── network / volumes ─────────────────────────────────────────────
    def network_create(self) -> None:
        self.run(["docker", "network", "create", "--label", f"lab.run={self.run_id}", network_name(self.run_id)])

    def volume_create(self, cell: int) -> None:
        self.run(["docker", "volume", "create", "--label", f"lab.run={self.run_id}", volume_name(self.run_id, cell)])

    # ── headscale cells ────────────────────────────────────────────────
    def cell_start(self, cell: int, config_path: Path, metrics_host_port: int, http_host_port: int) -> None:
        self.run(
            [
                "docker", "run", "-d",
                "--name", headscale_container_name(self.run_id, cell),
                "--network", network_name(self.run_id),
                "--label", f"lab.run={self.run_id}",
                "--memory", "512m",
                "--cpus", "0.5",
                "-v", f"{volume_name(self.run_id, cell)}:/var/lib/headscale",
                "-v", f"{config_path}:/etc/headscale/config.yaml:ro",
                "-p", f"127.0.0.1:{metrics_host_port}:9090",
                "-p", f"127.0.0.1:{http_host_port}:8080",
                HEADSCALE_IMAGE,
                "serve",
            ]
        )

    def cell_exec(self, cell: int, args: list[str], *, check: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
        return self.run(
            ["docker", "exec", headscale_container_name(self.run_id, cell), "headscale", *args],
            check=check,
            timeout=timeout,
        )

    def cell_health(self, cell: int) -> bool:
        return self.cell_exec(cell, ["nodes", "list"], check=False, timeout=20).returncode == 0

    def users_create(self, cell: int, user: str) -> None:
        self.cell_exec(cell, ["users", "create", user], check=False)

    def preauth_key_create(self, cell: int, user: str, *, ephemeral: bool = False) -> str:
        args = ["preauthkeys", "create", "--user", user, "--reusable=false", "--expiration", "10m", "--output", "json"]
        if ephemeral:
            args.append("--ephemeral")
        out = self.cell_exec(cell, args)
        try:
            data = json.loads(out.stdout)
            key = data.get("key") or data.get("preAuthKey", {}).get("key")
        except json.JSONDecodeError:
            key = None
        if not key:
            raise RuntimeError(f"could not parse preauth key from: {out.stdout[-200:]}")
        return key

    def nodes_list_json(self, cell: int) -> list[dict]:
        out = self.cell_exec(cell, ["nodes", "list", "--output", "json"], check=False)
        try:
            data = json.loads(out.stdout)
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []

    def connected_node_count(self, cell: int) -> int:
        return sum(1 for n in self.nodes_list_json(cell) if n.get("online"))

    # ── synthetic client nodes ─────────────────────────────────────────
    def client_start(self, cell: int, node: int, authkey: str, hostname: str) -> None:
        self.run(
            [
                "docker", "run", "-d",
                "--name", client_container_name(self.run_id, cell, node),
                "--network", network_name(self.run_id),
                "--label", f"lab.run={self.run_id}",
                "--memory", "128m",
                "--cpus", "0.2",
                "-e", f"TS_AUTHKEY={authkey}",
                "-e", f"TS_HOSTNAME={hostname}",
                "-e", "TS_USERSPACE=true",
                "-e", "TS_STATE_DIR=/tmp/ts",
                # Dial the cell's embedded lab DERP relay over plain HTTP on
                # the internal docker network (tailscale's built-in debug knob;
                # the same pattern as the merged THR-097 unit-B harness). The
                # relay is never disabled or bypassed; it stays internal-only.
                "-e", "TS_DEBUG_USE_DERP_HTTP=true",
                "-e", f"TS_EXTRA_ARGS=--login-server=http://hs-{self.run_id}-c{cell}:8080",
                TAILSCALE_IMAGE,
            ]
        )

    def client_status(self, cell: int, node: int) -> str:
        out = self.run(
            ["docker", "inspect", "--format", "{{.State.Status}}", client_container_name(self.run_id, cell, node)],
            check=False,
        )
        return out.stdout.strip()

    def client_rm(self, cell: int, node: int) -> None:
        self.run(["docker", "rm", "-f", client_container_name(self.run_id, cell, node)], check=False)

    # ── cell metrics / stats ───────────────────────────────────────────
    def cell_stats(self, cell: int) -> dict:
        name = headscale_container_name(self.run_id, cell)
        out = self.run(["docker", "stats", "--no-stream", "--format", "{{json .}}", name], check=False)
        try:
            data = json.loads(out.stdout)
        except json.JSONDecodeError:
            return {}
        return _parse_stats(data)

    def cell_disk_bytes(self, cell: int) -> int:
        name = headscale_container_name(self.run_id, cell)
        out = self.run(["docker", "inspect", "--format", "{{.SizeRw}}", name], check=False)
        try:
            return int(out.stdout.strip())
        except ValueError:
            return -1

    def volume_size_bytes(self, cell: int) -> int:
        """Per-cell state volume bytes via a read-only `du` sidecar.

        The docker volume's host path sits under the root-only
        ``/var/lib/docker`` tree, so a host-side walk fails on the runner
        with PermissionError (observed in lab run 33038215874). A one-shot
        read-only container using the already-pinned tailscale image (Alpine
        base: busybox `du`) measures the volume content through the docker
        daemon, which the runner user can reach.
        """
        out = self.run(
            [
                "docker", "run", "--rm",
                "--entrypoint", "du",
                "-v", f"{volume_name(self.run_id, cell)}:/data:ro",
                TAILSCALE_IMAGE,
                "-sb", "/data",
            ],
            check=False,
            timeout=60,
        )
        try:
            return int(out.stdout.strip().split()[0])
        except (ValueError, IndexError):
            return -1

    def cell_process_count(self, cell: int) -> int:
        name = headscale_container_name(self.run_id, cell)
        out = self.run(["docker", "top", name], check=False)
        lines = [ln for ln in out.stdout.splitlines() if ln.strip()]
        return max(0, len(lines) - 1)

    def apikey_create(self, cell: int) -> str:
        out = self.cell_exec(cell, ["apikeys", "create", "--expiration", "1h", "--output", "json"])
        try:
            data = json.loads(out.stdout)
        except json.JSONDecodeError:
            data = None
        # headscale 0.25 serializes the CreateApiKeyResponse.api_key string as a
        # bare JSON string ("prefix.hash"); the ApiKey proto carries no secret.
        key = None
        if isinstance(data, str):
            key = data
        elif isinstance(data, dict):
            key = data.get("apiKey") or data.get("api_key")
        if not key:
            raise RuntimeError(f"could not parse api key from: {out.stdout[-200:]}")
        return key

    def http_api_latency(self, http_host_port: int, api_key: str) -> dict:
        url = f"http://127.0.0.1:{http_host_port}/api/v1/node"
        t0 = time.monotonic()
        try:
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode("utf-8", "replace")
                status = resp.status
        except Exception as exc:  # noqa: BLE001
            return {"latency_ms": None, "status": None, "error": str(exc)[:100]}
        latency_ms = round((time.monotonic() - t0) * 1000, 3)
        try:
            nodes = json.loads(body).get("nodes", [])
            node_count = len(nodes)
        except json.JSONDecodeError:
            node_count = None
        return {"latency_ms": latency_ms, "status": status, "node_count": node_count}

    def metrics_scrape(self, metrics_host_port: int) -> str:
        url = f"http://127.0.0.1:{metrics_host_port}/metrics"
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                return resp.read().decode("utf-8", "replace")
        except Exception:
            return ""

    # ── host stats ─────────────────────────────────────────────────────
    def host_stats(self) -> dict:
        cpu = _host_cpu_percent(self)
        mem = _host_mem_percent()
        disk = shutil.disk_usage("/")
        return {
            "cpu_pct": cpu,
            "mem_pct": mem,
            "disk_pct": round(disk.used / disk.total * 100, 2),
            "disk_free_bytes": disk.free,
        }

    # ── teardown / residue ─────────────────────────────────────────────
    def teardown(self) -> None:
        label = f"lab.run={self.run_id}"
        self.run(["docker", "ps", "-aq", "--filter", f"label={label}"], check=False)
        containers = self.run(["docker", "ps", "-aq", "--filter", f"label={label}"], check=False)
        for cid in containers.stdout.split():
            self.run(["docker", "rm", "-f", cid], check=False)
        self.run(["docker", "network", "rm", network_name(self.run_id)], check=False)
        self.run(
            ["docker", "volume", "ls", "-q", "--filter", f"label={label}"],
            check=False,
        )
        volumes = self.run(["docker", "volume", "ls", "-q", "--filter", f"label={label}"], check=False)
        for vol in volumes.stdout.split():
            self.run(["docker", "volume", "rm", "-f", vol], check=False)

    def residue(self) -> dict:
        """Collect residue evidence without mutating anything."""
        label = f"lab.run={self.run_id}"
        containers = self.run(["docker", "ps", "-a", "--filter", f"label={label}", "--format", "{{json .}}"], check=False)
        networks = self.run(["docker", "network", "ls", "--filter", f"label={label}", "--format", "{{.Name}}"], check=False)
        volumes = self.run(["docker", "volume", "ls", "--filter", f"label={label}", "--format", "{{.Name}}"], check=False)
        pgrep = self.run(["pgrep", "-f", self.run_id], check=False)
        from cleanup import parse_docker_network_ls, parse_docker_ps_json, parse_docker_volume_ls, parse_pgrep

        # pgrep -f matches our own shell chain (the harness argv embeds the run
        # id); exclude the harness process and its ancestors, keep any other
        # run-id-matching pid as residue.
        own_chain = _own_chain_pids(os.getpid())
        pids = [p for p in parse_pgrep(pgrep.stdout) if p not in own_chain]
        return {
            "containers": [e.get("Names", "") for e in parse_docker_ps_json(containers.stdout) if e.get("Names")],
            "networks": parse_docker_network_ls(networks.stdout),
            "volumes": parse_docker_volume_ls(volumes.stdout),
            "pids": pids,
        }


def _own_chain_pids(own_pid: int) -> set[int]:
    """Return own_pid plus its ancestor pids (the harness's own shell chain)."""
    chain: set[int] = set()
    pid = own_pid
    while pid > 1:
        chain.add(pid)
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
            rest = stat.split(")", 1)[1]
            ppid = int(rest.split()[1])
        except (OSError, IndexError, ValueError):
            break
        if ppid == pid:
            break
        pid = ppid
    return chain


def _parse_stats(data: dict) -> dict:
    def _to_bytes(s: str) -> float:
        s = s.strip()
        try:
            num = float(s.split()[0])
            unit = s.split()[1].upper() if len(s.split()) > 1 else "B"
        except (ValueError, IndexError):
            return -1.0
        mult = {"B": 1, "KIB": 1024, "MIB": 1024**2, "GIB": 1024**3}
        return num * mult.get(unit, 1)

    def _to_pct(s: str) -> float:
        try:
            return float(s.strip().rstrip("%"))
        except ValueError:
            return -1.0

    cpu = data.get("CPUPerc", "-1%")
    mem_usage = data.get("MemUsage", "-1 / -1")
    return {
        "cpu_pct": _to_pct(cpu),
        "mem_usage_bytes": _to_bytes(mem_usage.split("/")[0]) if "/" in mem_usage else -1.0,
        "mem_limit_bytes": _to_bytes(mem_usage.split("/")[1]) if "/" in mem_usage else -1.0,
    }


def _host_cpu_percent(docker: Docker) -> float:
    """CPU% over the last ~1s via /proc/stat deltas (two samples)."""
    def _read() -> tuple[int, int]:
        with open("/proc/stat", encoding="utf-8") as fh:
            parts = fh.readline().split()
        idle = int(parts[4]) + int(parts[5]) if len(parts) > 5 else int(parts[4])
        total = sum(int(p) for p in parts[1:])
        return idle, total

    idle0, total0 = _read()
    time.sleep(1.0)
    idle1, total1 = _read()
    d_idle = idle1 - idle0
    d_total = total1 - total0
    if d_total <= 0:
        return 0.0
    return round((1.0 - d_idle / d_total) * 100, 2)


def _host_mem_percent() -> float:
    with open("/proc/meminfo", encoding="utf-8") as fh:
        mem = {}
        for line in fh:
            k, _, v = line.partition(":")
            mem[k] = int(v.strip().split()[0])
    total = mem.get("MemTotal", 1)
    available = mem.get("MemAvailable", total)
    return round((total - available) / total * 100, 2)
