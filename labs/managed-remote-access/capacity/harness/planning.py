"""Scenario planning: deterministic, bounded load steps for the lab.

All step sequences are fixed constants (no runtime discovery), bounded by
the lab acceptance gates in REPORT.md: max 4 cells per host, max 64 nodes
per cell, max 128 concurrent nodes per host on the disposable 4-vCPU /
16 GiB runner.
"""

from __future__ import annotations

from typing import Iterable


def plan_idle_cells() -> list[int]:
    """Cells-per-host load steps for the idle overhead scenario."""
    return [1, 2, 4]


def plan_single_cell_nodes() -> list[int]:
    """Nodes-per-cell steps for the single-cell scaling scenario."""
    return [8, 16, 32, 64]


def plan_multi_cell_steps() -> list[tuple[int, list[int]]]:
    """(cells, nodes_per_cell) steps for the cells-per-host scenario."""
    return [(2, [8, 16, 32]), (4, [8, 16])]


def plan_churn_waves() -> list[tuple[int, int]]:
    """(nodes_per_wave, wave_count) for the churn scenario."""
    return [(16, 2)]


def restart_node_count() -> int:
    """Nodes connected before the restart-recovery scenario."""
    return 32


def all_node_steps() -> Iterable[tuple[int, int]]:
    """Yield (cells, nodes_per_cell) for every loaded step in order."""
    for n in plan_single_cell_nodes():
        yield (1, n)
    for cells, nodes in plan_multi_cell_steps():
        for n in nodes:
            yield (cells, n)
