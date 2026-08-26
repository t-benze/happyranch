"""Prometheus text-format parsing and histogram quantile estimation.

Used to turn each headscale cell's ``/metrics`` scrape into control-plane
latency evidence: headscale's own ``headscale_http_duration_seconds``
histogram and ``headscale_mapresponse_sent_total`` counter.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict

_LINE_RE = re.compile(
    r'^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?P<labels>\{.*\})? (?P<value>-?[0-9.eE+-]+)$'
)
_LABEL_RE = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*"([^"]*)"')


def parse_prometheus(text: str) -> dict[str, list[tuple[dict[str, str], float]]]:
    """Parse a Prometheus text exposition into {name: [(labels, value), ...]}."""
    out: dict[str, list[tuple[dict[str, str], float]]] = defaultdict(list)
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        name = m.group("name")
        labels: dict[str, str] = {}
        raw_labels = m.group("labels")
        if raw_labels:
            for k, v in _LABEL_RE.findall(raw_labels):
                labels[k] = v
        try:
            value = float(m.group("value"))
        except ValueError:
            continue
        out[name].append((labels, value))
    return dict(out)


def histogram_quantile(buckets: dict[float, float], count_total: float, q: float) -> float | None:
    """Estimate a quantile from cumulative histogram buckets (prometheus method)."""
    if count_total <= 0:
        return None
    if q < 0 or q > 1:
        return None
    target = q * count_total
    bounds = sorted(b for b in buckets if math.isfinite(b))
    if not bounds:
        return None
    # First bucket boundary.
    if target <= 0:
        return float(bounds[0])
    cumulative = 0.0
    prev_bound = bounds[0]
    prev_cum = 0.0
    for bound in bounds:
        cum = buckets.get(bound, 0.0)
        if cum >= target:
            if prev_cum == cum:
                return float(prev_bound)
            frac = (target - prev_cum) / (cum - prev_cum)
            return float(prev_bound + frac * (bound - prev_bound))
        prev_bound = bound
        prev_cum = cum
    # Above the last finite bucket: extrapolate to the +Inf bucket if present.
    if float("inf") in buckets and buckets[float("inf")] > prev_cum:
        frac = (target - prev_cum) / (buckets[float("inf")] - prev_cum)
        return float(prev_bound + frac * (10.0 * prev_bound if prev_bound else 1.0))
    return float(prev_bound)


def extract_http_duration_histogram(
    parsed: dict[str, list[tuple[dict[str, str], float]]], path: str
) -> tuple[dict[float, float], float, float]:
    """Extract {bucket_le: cumulative} , count, sum for a given path template."""
    buckets: dict[float, float] = {}
    count = 0.0
    total = 0.0
    prefix = "headscale_http_duration_seconds"
    for labels, value in parsed.get(f"{prefix}_bucket", []):
        if labels.get("path") == path:
            le = labels.get("le", "")
            key = float("inf") if le == "+Inf" else float(le)
            buckets[key] = value
    for labels, value in parsed.get(f"{prefix}_count", []):
        if labels.get("path") == path:
            count = value
    for labels, value in parsed.get(f"{prefix}_sum", []):
        if labels.get("path") == path:
            total = value
    return buckets, count, total


def counter_rate(samples: list[tuple[float, float]]) -> float | None:
    """Rate of a counter over time: (last - first) / (t_last - t_first)."""
    if len(samples) < 2:
        return None
    (t0, v0), (t1, v1) = samples[0], samples[-1]
    dt = t1 - t0
    if dt <= 0:
        return None
    return (v1 - v0) / dt
