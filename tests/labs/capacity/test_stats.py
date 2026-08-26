"""Unit tests for statistics helpers: quantiles and baseline subtraction."""

from __future__ import annotations

import pytest

from stats import mean, quantiles, stdev, subtract_baseline


def test_quantiles_known_values():
    values = [1.0, 2.0, 3.0, 4.0]
    q = quantiles(values, (0.5, 0.9))
    assert q["p50"] == pytest.approx(2.5)
    assert q["p90"] == pytest.approx(3.7)


def test_quantiles_single_value():
    assert quantiles([7.0], (0.5, 0.99)) == {"p50": 7.0, "p99": 7.0}


def test_quantiles_empty():
    assert quantiles([], (0.5,)) == {"p50": None}


def test_quantiles_unsorted_input():
    q = quantiles([4.0, 1.0, 3.0, 2.0], (0.5,))
    assert q["p50"] == pytest.approx(2.5)


def test_mean_stdev():
    vals = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
    assert mean(vals) == pytest.approx(5.0)
    assert stdev(vals) == pytest.approx(2.0)


def test_subtract_baseline_clamps_at_zero():
    loaded = [5.0, 8.0, 6.0]
    baseline = [2.0, 4.0]  # mean 3.0
    result = subtract_baseline(loaded, baseline)
    assert result == pytest.approx([2.0, 5.0, 3.0])


def test_subtract_baseline_negative_clamped():
    loaded = [1.0, 2.0]
    baseline = [10.0]  # mean 10.0 -> all deltas negative
    result = subtract_baseline(loaded, baseline)
    assert result == pytest.approx([0.0, 0.0])


def test_subtract_baseline_empty_baseline_returns_input():
    assert subtract_baseline([1.0, 2.0], []) == pytest.approx([1.0, 2.0])
