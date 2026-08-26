"""Unit tests for scenario planning bounds (deterministic load steps)."""

from __future__ import annotations

from planning import (
    plan_churn_waves,
    plan_idle_cells,
    plan_multi_cell_steps,
    plan_single_cell_nodes,
    restart_node_count,
)


def test_idle_cells_bounded():
    cells = plan_idle_cells()
    assert cells == [1, 2, 4]
    assert max(cells) <= 4


def test_single_cell_nodes_bounded():
    nodes = plan_single_cell_nodes()
    assert nodes == [8, 16, 32, 64]
    assert max(nodes) <= 64
    assert all(n > 0 for n in nodes)


def test_multi_cell_steps_respect_bounds():
    steps = plan_multi_cell_steps()
    assert steps == [(2, [8, 16, 32]), (4, [8, 16])]
    for cells, nodes in steps:
        assert cells <= 4
        assert max(nodes) <= 64
        assert cells * max(nodes) <= 128  # host node cap


def test_churn_waves_bounded():
    waves = plan_churn_waves()
    assert waves == [(16, 2)]
    for nodes, waves_count in waves:
        assert nodes <= 16
        assert 1 <= waves_count <= 3


def test_restart_node_count_bounded():
    assert restart_node_count() == 32
