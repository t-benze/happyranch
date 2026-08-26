"""Scenario orchestration for the capacity lab.

Each scenario is deterministic (fixed planning constants), bounded (abort
gates), and leaves no residue (teardown + residue check after every
scenario and on abort). Raw evidence streams to machine-readable JSONL
files under the run results dir; summary JSON per scenario.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from cellspec import headscale_config, lab_ports
from cleanup import residue_report
from dockerctl import Docker, Transcript
from gates import (
    LabLimits,
    evaluate_cell_gates,
    evaluate_connected_gate,
    evaluate_enrollment_gate,
    evaluate_host_gates,
)
from metrics import counter_rate, extract_http_duration_histogram, histogram_quantile, parse_prometheus
from models import headscale_container_name, state_dir_name
from planning import (
    all_node_steps,
    plan_churn_waves,
    plan_idle_cells,
    plan_multi_cell_steps,
    restart_node_count,
)
from stats import mean, quantiles, stdev, subtract_baseline

LAB_USER = "labuser"
SAMPLE_INTERVAL_S = 5
SAMPLE_WINDOW_S = 60
WARMUP_S = 30
ENROLL_TIMEOUT_S = 45
CONNECT_ALL_TIMEOUT_S = 120
NODE_CONNECT_POLL_S = 1.0


@dataclass
class ScenarioResult:
    name: str
    ok: bool = True
    aborts: list[str] = field(default_factory=list)
    summary: dict = field(default_factory=dict)


class Runner:
    def __init__(self, run_id: str, out_dir: Path, limits: LabLimits | None = None):
        self.run_id = run_id
        self.out_dir = out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.limits = limits or LabLimits()
        self.transcript = Transcript(out_dir / "transcript.jsonl")
        self.docker = Docker(self.transcript, run_id)
        self.samples_path = out_dir / "samples.jsonl"
        self.enroll_path = out_dir / "enroll.jsonl"
        self.host_series: list[tuple[str, dict]] = []
        self.cell_series: list[dict] = []

    # ── low-level helpers ──────────────────────────────────────────────
    def _emit(self, path: Path, obj: dict) -> None:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(obj, sort_keys=True) + "\n")

    def _samples(self, scenario: str, step: dict) -> None:
        for ts, host in self.host_series:
            self._emit(self.samples_path, {"run_id": self.run_id, "scenario": scenario, "step": step, "scope": "host", "ts": ts, **host})
        for s in self.cell_series:
            self._emit(self.samples_path, {"run_id": self.run_id, "scenario": scenario, "step": step, "scope": "cell", **s})
        self.host_series = []
        self.cell_series = []

    def _sample_once(self, cells: list[int], metrics_ports: dict[int, int]) -> None:
        ts = time.time()
        self.host_series.append((ts, self.docker.host_stats()))
        for cell in cells:
            stats = self.docker.cell_stats(cell)
            row = {
                "cell": headscale_container_name(self.run_id, cell),
                "rss_bytes": stats.get("mem_usage_bytes", -1.0),
                "cpu_pct": stats.get("cpu_pct", -1.0),
                "proc_count": self.docker.cell_process_count(cell),
                "disk_bytes": self.docker.cell_disk_bytes(cell),
                "volume_bytes": self.docker.volume_size_bytes(cell),
                "connected_nodes": self.docker.connected_node_count(cell),
                "metrics": self._metrics_summary(metrics_ports[cell]),
            }
            self.cell_series.append(row)

    def _metrics_summary(self, metrics_port: int) -> dict:
        text = self.docker.metrics_scrape(metrics_port)
        parsed = parse_prometheus(text)
        out: dict = {}
        for path in ("/api/v1/node", "/api/v1/preauthkey", "/api/v1/user", "/machine"):
            buckets, count, total = extract_http_duration_histogram(parsed, path)
            if count > 0:
                q = histogram_quantile(buckets, count, 0.95)
                out[path] = {
                    "count": count,
                    "sum_s": total,
                    "mean_s": round(total / count, 6) if count else None,
                    "p95_s": round(q, 6) if q is not None else None,
                }
        map_total = parsed.get("headscale_mapresponse_sent_total", [])
        if map_total:
            labels = sorted({tuple(sorted(l.items())) for l, _ in map_total})
            out["mapresponse_sent_total"] = {"labels": [dict(l) for l in labels], "values": [v for _, v in map_total]}
        return out

    def _abort_check(self, scenario: str, step: dict, fail_ratio: float | None = None, connected_ratio: float | None = None) -> list[str]:
        aborts: list[str] = []
        for a in evaluate_host_gates(self.host_series, self.limits):
            aborts.append(str(a))
        for a in evaluate_cell_gates(self.cell_series, self.limits):
            aborts.append(str(a))
        if fail_ratio is not None:
            for a in evaluate_enrollment_gate(fail_ratio, self.limits):
                aborts.append(str(a))
        if connected_ratio is not None:
            for a in evaluate_connected_gate(connected_ratio, self.limits):
                aborts.append(str(a))
        if aborts:
            self.transcript.record(["abort-gate", scenario, json.dumps(step)], 1, 0, "; ".join(aborts))
        return aborts

    def _wait_connected(self, cell: int, expected: int, timeout_s: int) -> tuple[bool, int]:
        deadline = time.monotonic() + timeout_s
        last = 0
        while time.monotonic() < deadline:
            last = self.docker.connected_node_count(cell)
            if last >= expected:
                return True, last
            time.sleep(NODE_CONNECT_POLL_S)
        return False, last

    def _start_cell(self, cell: int) -> dict:
        self.docker.volume_create(cell)
        cfg_path = self.out_dir / f"config-c{cell}.yaml"
        cfg_path.write_text(headscale_config(self.run_id, cell), encoding="utf-8")
        _http, _grpc, metrics = lab_ports(cell)
        self.docker.cell_start(cell, cfg_path, metrics)
        return {"metrics_port": metrics}

    def _enroll(self, cell: int, node: int, *, ephemeral: bool) -> tuple[float, bool]:
        """Start one synthetic client; returns (latency_ms, ok)."""
        hostname = f"n{node}"
        key = self.docker.preauth_key_create(cell, LAB_USER, ephemeral=ephemeral)
        t0 = time.monotonic()
        try:
            self.docker.client_start(cell, node, key, hostname)
        except RuntimeError:
            return -1.0, False
        deadline = time.monotonic() + ENROLL_TIMEOUT_S
        while time.monotonic() < deadline:
            online = [n for n in self.docker.nodes_list_json(cell) if n.get("givenName") == hostname and n.get("online")]
            if online:
                latency_ms = round((time.monotonic() - t0) * 1000, 1)
                self._emit(self.enroll_path, {
                    "run_id": self.run_id, "cell": cell, "node": node, "ephemeral": ephemeral,
                    "latency_ms": latency_ms, "ok": True,
                })
                return latency_ms, True
            time.sleep(NODE_CONNECT_POLL_S)
        self._emit(self.enroll_path, {"run_id": self.run_id, "cell": cell, "node": node, "ephemeral": ephemeral, "latency_ms": None, "ok": False})
        return None, False

    def _teardown_and_residue(self, scenario: str) -> dict:
        self.docker.teardown()
        time.sleep(2)
        res = self.docker.residue()
        state_dir = self.out_dir / state_dir_name(self.run_id)
        state_entries = [p.name for p in state_dir.iterdir()] if state_dir.exists() else []
        report = residue_report(
            containers=res["containers"], networks=res["networks"], volumes=res["volumes"],
            pids=res["pids"], state_entries=state_entries, run_id=self.run_id,
        )
        report["scenario"] = scenario
        self._emit(self.out_dir / "residue.jsonl", report)
        return report

    # ── scenarios ──────────────────────────────────────────────────────
    def run_idle(self) -> ScenarioResult:
        result = ScenarioResult("idle")
        # Host baseline before any container (baseline subtraction source).
        for _ in range(6):
            ts = time.time()
            self.host_series.append((ts, self.docker.host_stats()))
            time.sleep(SAMPLE_INTERVAL_S)
        baseline = [s["cpu_pct"] for _, s in self.host_series]
        self.host_series = []
        self._emit(self.out_dir / "baseline.jsonl", {"run_id": self.run_id, "host_cpu_pct_samples": baseline})

        try:
            self.docker.network_create()
            for cells in plan_idle_cells():
                ports: dict[int, int] = {}
                cell_names = []
                for c in range(1, cells + 1):
                    info = self._start_cell(c)
                    ports[c] = info["metrics_port"]
                    cell_names.append(c)
                # wait for healthy
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    if all(self.docker.cell_health(c) for c in cell_names):
                        break
                    time.sleep(2)
                time.sleep(WARMUP_S)
                deadline = time.monotonic() + SAMPLE_WINDOW_S
                while time.monotonic() < deadline:
                    self._sample_once(cell_names, ports)
                    aborts = self._abort_check("idle", {"cells": cells})
                    if aborts:
                        result.aborts.extend(aborts)
                        result.ok = False
                        break
                    time.sleep(SAMPLE_INTERVAL_S)
                self._samples("idle", {"cells": cells})
                for c in cell_names:
                    self.docker.run(["docker", "rm", "-f", headscale_container_name(self.run_id, c)], check=False)
            result.summary = {"baseline_host_cpu_mean_pct": round(mean(baseline), 3)}
        except Exception as exc:  # noqa: BLE001 — lab harness surfaces any failure
            result.ok = False
            result.summary["error"] = str(exc)
        finally:
            self._teardown_and_residue("idle")
        self._write_summary(result)
        return result

    def run_nodes(self) -> ScenarioResult:
        result = ScenarioResult("nodes")
        try:
            self.docker.network_create()
            for cells, nodes_per_cell in all_node_steps():
                step = {"cells": cells, "nodes_per_cell": nodes_per_cell}
                ports: dict[int, int] = {}
                cell_names = list(range(1, cells + 1))
                for c in cell_names:
                    info = self._start_cell(c)
                    ports[c] = info["metrics_port"]
                    self.docker.users_create(c, LAB_USER)
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    if all(self.docker.cell_health(c) for c in cell_names):
                        break
                    time.sleep(2)
                fails = 0
                for c in cell_names:
                    for node in range(1, nodes_per_cell + 1):
                        _lat, ok = self._enroll(c, node, ephemeral=False)
                        if not ok:
                            fails += 1
                fail_ratio = fails / (cells * nodes_per_cell)
                connected_ok, connected = True, cells * nodes_per_cell
                if fail_ratio <= self.limits.enroll_fail_max:
                    for c in cell_names:
                        ok_c, connected_c = self._wait_connected(c, nodes_per_cell, CONNECT_ALL_TIMEOUT_S)
                        connected_ok = connected_ok and ok_c
                        connected = connected_c
                time.sleep(WARMUP_S)
                deadline = time.monotonic() + SAMPLE_WINDOW_S
                while time.monotonic() < deadline:
                    self._sample_once(cell_names, ports)
                    aborts = self._abort_check("nodes", step, fail_ratio=fail_ratio, connected_ratio=(connected / (cells * nodes_per_cell)) if cells * nodes_per_cell else 0.0)
                    if aborts:
                        result.aborts.extend(aborts)
                        result.ok = False
                        break
                    time.sleep(SAMPLE_INTERVAL_S)
                self._samples("nodes", step)
                # teardown clients + cells for this step
                for c in cell_names:
                    for node in range(1, nodes_per_cell + 1):
                        self.docker.client_rm(c, node)
                    self.docker.run(["docker", "rm", "-f", headscale_container_name(self.run_id, c)], check=False)
                result.summary[str(step)] = {"fail_ratio": round(fail_ratio, 4), "connected": connected}
        except Exception as exc:  # noqa: BLE001
            result.ok = False
            result.summary["error"] = str(exc)
        finally:
            self._teardown_and_residue("nodes")
        self._write_summary(result)
        return result

    def run_churn(self) -> ScenarioResult:
        result = ScenarioResult("churn")
        try:
            self.docker.network_create()
            cell = 1
            info = self._start_cell(cell)
            self.docker.users_create(cell, LAB_USER)
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if self.docker.cell_health(cell):
                    break
                time.sleep(2)
            db_before = self.docker.volume_size_bytes(cell)
            for wave, (nodes, waves) in enumerate(plan_churn_waves(), start=1):
                for _ in range(waves):
                    for node in range(1, nodes + 1):
                        _lat, ok = self._enroll(cell, node, ephemeral=True)
                        if not ok:
                            result.summary.setdefault("enroll_failures", []).append(node)
                    self._wait_connected(cell, nodes, CONNECT_ALL_TIMEOUT_S)
                    time.sleep(SAMPLE_WINDOW_S)
                    # churn: kill all clients; wait for headscale to expire them
                    for node in range(1, nodes + 1):
                        self.docker.client_rm(cell, node)
                    # ephemeral inactivity timeout 75s -> poll for cleanup (bounded)
                    expiry_deadline = time.monotonic() + 180
                    while time.monotonic() < expiry_deadline:
                        left = self.docker.connected_node_count(cell)
                        if left == 0:
                            break
                        time.sleep(5)
                    result.summary[f"wave_{wave}_leftover_nodes"] = self.docker.connected_node_count(cell)
                    time.sleep(WARMUP_S)
                    self._sample_once([cell], {cell: info["metrics_port"]})
                    self._samples("churn", {"wave": wave})
                    aborts = self._abort_check("churn", {"wave": wave})
                    if aborts:
                        result.aborts.extend(aborts)
                        result.ok = False
                        break
            db_after = self.docker.volume_size_bytes(cell)
            result.summary["db_bytes_before_waves"] = db_before
            result.summary["db_bytes_after_waves"] = db_after
            result.summary["db_growth_bytes"] = db_after - db_before
        except Exception as exc:  # noqa: BLE001
            result.ok = False
            result.summary["error"] = str(exc)
        finally:
            self._teardown_and_residue("churn")
        self._write_summary(result)
        return result

    def run_restart(self) -> ScenarioResult:
        result = ScenarioResult("restart")
        try:
            self.docker.network_create()
            cell = 1
            info = self._start_cell(cell)
            self.docker.users_create(cell, LAB_USER)
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if self.docker.cell_health(cell):
                    break
                time.sleep(2)
            n = restart_node_count()
            for node in range(1, n + 1):
                _lat, ok = self._enroll(cell, node, ephemeral=False)
                if not ok:
                    result.summary["enroll_failures"] = result.summary.get("enroll_failures", 0) + 1
            self._wait_connected(cell, n, CONNECT_ALL_TIMEOUT_S)
            time.sleep(WARMUP_S)
            # SIGKILL the cell then restart it
            t_kill = time.monotonic()
            self.docker.run(["docker", "kill", "-s", "KILL", headscale_container_name(self.run_id, cell)], check=False)
            t_healthy: float | None = None
            healthy_deadline = time.monotonic() + 120
            while time.monotonic() < healthy_deadline:
                if self.docker.cell_health(cell):
                    t_healthy = time.monotonic()
                    break
                time.sleep(1)
            t_all_online: float | None = None
            online_deadline = (t_healthy or time.monotonic()) + 180
            while time.monotonic() < online_deadline:
                if self.docker.connected_node_count(cell) >= n:
                    t_all_online = time.monotonic()
                    break
                time.sleep(1)
            result.summary["kill_to_healthy_s"] = round(t_healthy - t_kill, 1) if t_healthy else None
            result.summary["kill_to_all_online_s"] = round(t_all_online - t_kill, 1) if t_all_online else None
            result.summary["nodes_expected"] = n
            result.summary["nodes_online_final"] = self.docker.connected_node_count(cell)
        except Exception as exc:  # noqa: BLE001
            result.ok = False
            result.summary["error"] = str(exc)
        finally:
            self._teardown_and_residue("restart")
        self._write_summary(result)
        return result

    def run_failure(self) -> ScenarioResult:
        result = ScenarioResult("failure")
        try:
            self.docker.network_create()
            cell = 1
            info = self._start_cell(cell)
            self.docker.users_create(cell, LAB_USER)
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if self.docker.cell_health(cell):
                    break
                time.sleep(2)
            for node in range(1, 9):
                _lat, ok = self._enroll(cell, node, ephemeral=False)
                if not ok:
                    result.summary["enroll_failures"] = result.summary.get("enroll_failures", 0) + 1
            self._wait_connected(cell, 8, CONNECT_ALL_TIMEOUT_S)
            # kill the control plane; clients should drop to offline
            self.docker.run(["docker", "kill", "-s", "KILL", headscale_container_name(self.run_id, cell)], check=False)
            time.sleep(10)
            offline = self.docker.connected_node_count(cell)
            result.summary["nodes_online_after_kill"] = offline
            # process/container residue of the killed cell (state=exited is expected)
            result.summary["cell_container_state_after_kill"] = self.docker.run(
                ["docker", "inspect", "--format", "{{.State.Status}}", headscale_container_name(self.run_id, cell)], check=False
            ).stdout.strip()
        except Exception as exc:  # noqa: BLE001
            result.ok = False
            result.summary["error"] = str(exc)
        finally:
            self._teardown_and_residue("failure")
        self._write_summary(result)
        return result

    def _write_summary(self, result: ScenarioResult) -> None:
        path = self.out_dir / f"{result.name}.summary.json"
        with path.open("w", encoding="utf-8") as fh:
            json.dump(
                {
                    "run_id": self.run_id,
                    "scenario": result.name,
                    "ok": result.ok,
                    "aborts": result.aborts,
                    "summary": result.summary,
                    "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                },
                fh,
                indent=2,
                sort_keys=True,
            )
