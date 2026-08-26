"""Statistics helpers: quantiles, mean, stdev, baseline subtraction."""

from __future__ import annotations

import math
import statistics
from typing import Iterable, Sequence


def mean(values: Sequence[float]) -> float:
    if not values:
        return float("nan")
    return statistics.fmean(values)


def stdev(values: Sequence[float]) -> float:
    """Population standard deviation of the observed sample set."""
    if len(values) < 2:
        return 0.0
    return statistics.pstdev(values)


def quantiles(values: Sequence[float], qs: Iterable[float] = (0.5, 0.9, 0.95, 0.99)) -> dict[str, float | None]:
    """Linear-interpolated quantiles (numpy default semantics)."""
    out: dict[str, float | None] = {}
    if not values:
        for q in qs:
            out[f"p{int(round(q * 100))}"] = None
        return out
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    for q in qs:
        if n == 1:
            out[f"p{int(round(q * 100))}"] = float(sorted_vals[0])
            continue
        pos = q * (n - 1)
        lo = math.floor(pos)
        hi = math.ceil(pos)
        frac = pos - lo
        val = sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac
        out[f"p{int(round(q * 100))}"] = float(val)
    return out


def subtract_baseline(values: Sequence[float], baseline: Sequence[float]) -> list[float]:
    """Subtract the baseline mean from each value, clamped at 0 (for CPU %)."""
    if not baseline:
        return [float(v) for v in values]
    base = statistics.fmean(baseline)
    return [max(0.0, float(v) - base) for v in values]
