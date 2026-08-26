"""Abort-gate logic — exploratory LAB ACCEPTANCE GATES, never product SLOs.

These thresholds bound the experiment so a pathological load step or a
host/runtime failure stops the run deterministically instead of exhausting
the disposable lab VM. They are justified in REPORT.md as exploratory
laboratory gates for the GitHub Actions hosted runner (exact host facts in
results/<run_id>/env.json), not as capacity or SLA commitments.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AbortGate:
    name: str
    reason: str
    cell: str | None = None

    def __str__(self) -> str:
        return f"ABORT[{self.name}]: {self.reason}"


@dataclass(frozen=True)
class LabLimits:
    host_cpu_max_pct: float = 90.0
    host_mem_max_pct: float = 85.0
    host_disk_max_pct: float = 50.0
    cell_rss_max_bytes: float = 1.5 * 1024**3
    enroll_fail_max: float = 0.10
    connected_min: float = 0.90
    sustained: int = 3


def _sustained_over(series: list[dict], key: str, limit: float, sustained: int) -> bool:
    """True when the last ``sustained`` samples are all over ``limit``."""
    recent = series[-sustained:] if len(series) >= sustained else series
    return len(recent) == sustained and all(s.get(key, 0.0) > limit for s in recent)


def evaluate_host_gates(
    host_series: list[tuple[str, dict]], limits: LabLimits
) -> list[AbortGate]:
    """Evaluate sustained host CPU/mem/disk abort gates over a sample series."""
    series = [s for _, s in host_series]
    aborts: list[AbortGate] = []
    if _sustained_over(series, "cpu_pct", limits.host_cpu_max_pct, limits.sustained):
        aborts.append(AbortGate("host_cpu", f"host CPU > {limits.host_cpu_max_pct}% sustained"))
    if _sustained_over(series, "mem_pct", limits.host_mem_max_pct, limits.sustained):
        aborts.append(AbortGate("host_mem", f"host memory > {limits.host_mem_max_pct}% sustained"))
    if _sustained_over(series, "disk_pct", limits.host_disk_max_pct, limits.sustained):
        aborts.append(AbortGate("host_disk", f"host disk > {limits.host_disk_max_pct}% sustained"))
    return aborts


def evaluate_cell_gates(
    cell_samples: list[dict], limits: LabLimits
) -> list[AbortGate]:
    """Evaluate per-cell RSS abort gates. Each dict: {cell, rss_bytes, ...}."""
    by_cell: dict[str, list[float]] = {}
    for s in cell_samples:
        by_cell.setdefault(s["cell"], []).append(float(s.get("rss_bytes", 0.0)))
    aborts: list[AbortGate] = []
    for cell, rss in by_cell.items():
        recent = rss[-limits.sustained:] if len(rss) >= limits.sustained else rss
        if len(recent) == limits.sustained and all(v > limits.cell_rss_max_bytes for v in recent):
            aborts.append(
                AbortGate(
                    "cell_rss",
                    f"cell RSS > {limits.cell_rss_max_bytes / (1024**3):.2f} GiB sustained",
                    cell=cell,
                )
            )
    return aborts


def evaluate_enrollment_gate(fail_ratio: float, limits: LabLimits) -> list[AbortGate]:
    if fail_ratio > limits.enroll_fail_max:
        return [AbortGate("enroll_fail", f"enrollment failure ratio {fail_ratio:.2f} > {limits.enroll_fail_max}")]
    return []


def evaluate_connected_gate(connected_ratio: float, limits: LabLimits) -> list[AbortGate]:
    if connected_ratio < limits.connected_min:
        return [AbortGate("connected_ratio", f"connected ratio {connected_ratio:.2f} < {limits.connected_min}")]
    return []
