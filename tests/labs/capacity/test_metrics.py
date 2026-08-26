"""Unit tests for Prometheus metrics parsing and histogram quantile math.

The lab scrapes each headscale cell's ``/metrics`` endpoint. The
server-side control-plane latency evidence comes from headscale's own
``headscale_http_duration_seconds`` histogram, and map traffic from
``headscale_mapresponse_sent_total`` counter rate.
"""

from __future__ import annotations

import pytest

from metrics import (
    extract_http_duration_histogram,
    histogram_quantile,
    parse_prometheus,
    counter_rate,
)

SAMPLE_SCRAPE = """\
# HELP headscale_http_duration_seconds Duration of HTTP requests.
# TYPE headscale_http_duration_seconds histogram
headscale_http_duration_seconds_bucket{path="/api/v1/node",le="0.005"} 10
headscale_http_duration_seconds_bucket{path="/api/v1/node",le="0.01"} 20
headscale_http_duration_seconds_bucket{path="/api/v1/node",le="0.025"} 30
headscale_http_duration_seconds_bucket{path="/api/v1/node",le="0.05"} 35
headscale_http_duration_seconds_bucket{path="/api/v1/node",le="0.1"} 40
headscale_http_duration_seconds_bucket{path="/api/v1/node",le="0.25"} 42
headscale_http_duration_seconds_bucket{path="/api/v1/node",le="0.5"} 44
headscale_http_duration_seconds_bucket{path="/api/v1/node",le="1"} 44
headscale_http_duration_seconds_bucket{path="/api/v1/node",le="2.5"} 45
headscale_http_duration_seconds_bucket{path="/api/v1/node",le="5"} 46
headscale_http_duration_seconds_bucket{path="/api/v1/node",le="10"} 46
headscale_http_duration_seconds_bucket{path="/api/v1/node",le="+Inf"} 46
headscale_http_duration_seconds_sum{path="/api/v1/node"} 1.234
headscale_http_duration_seconds_count{path="/api/v1/node"} 46
# HELP headscale_mapresponse_sent_total total count of mapresponses sent to clients
# TYPE headscale_mapresponse_sent_total counter
headscale_mapresponse_sent_total{status="ok",type="update"} 5
headscale_mapresponse_sent_total{status="ok",type="full"} 100
"""


def test_parse_prometheus_names():
    parsed = parse_prometheus(SAMPLE_SCRAPE)
    assert "headscale_http_duration_seconds_bucket" in parsed
    assert "headscale_mapresponse_sent_total" in parsed


def test_extract_http_duration_histogram():
    parsed = parse_prometheus(SAMPLE_SCRAPE)
    buckets, count, total = extract_http_duration_histogram(
        parsed, "/api/v1/node"
    )
    assert count == 46
    assert total == pytest.approx(1.234)
    assert buckets[0.005] == 10
    assert buckets[float("inf")] == 46


def test_histogram_quantile_median():
    # 46 samples; p50 falls inside the 0.01 bucket (cumulative 20 >= 23 at 0.025)
    # cumulative at le=0.01 is 20 < 23; at le=0.025 is 30 >= 23 -> interpolate.
    buckets = {0.005: 10, 0.01: 20, 0.025: 30, 0.05: 35, 0.1: 40, 0.25: 42, 0.5: 44, 1.0: 44, 2.5: 45, 5.0: 46, 10.0: 46, float("inf"): 46}
    q = histogram_quantile(buckets, 46, 0.5)
    # rank = 0.5*46 = 23 -> bucket (0.01, 0.025]: cumulative prev 20, cur 30
    assert q == pytest.approx(0.01 + (23 - 20) / (30 - 20) * (0.025 - 0.01))
    assert 0.01 < q < 0.025


def test_histogram_quantile_empty():
    assert histogram_quantile({float("inf"): 0}, 0, 0.5) is None


def test_histogram_quantile_q0_q1():
    buckets = {0.005: 10, float("inf"): 10}
    assert histogram_quantile(buckets, 10, 0.0) == pytest.approx(0.005)
    assert histogram_quantile(buckets, 10, 1.0) == pytest.approx(0.005)


def test_parse_http_histogram_with_real_gateway_label():
    # Headscale 0.25 records the gRPC-gateway surface under the "/api/v1/"
    # route prefix; /ts2021 and /machine/map are excluded by the middleware.
    text = (
        '# TYPE headscale_http_duration_seconds histogram\n'
        'headscale_http_duration_seconds_bucket{path="/api/v1/",le="0.01"} 5\n'
        'headscale_http_duration_seconds_bucket{path="/api/v1/",le="0.05"} 9\n'
        'headscale_http_duration_seconds_bucket{path="/api/v1/",le="+Inf"} 10\n'
        'headscale_http_duration_seconds_sum{path="/api/v1/"} 0.12\n'
        'headscale_http_duration_seconds_count{path="/api/v1/"} 10\n'
    )
    parsed = parse_prometheus(text)
    buckets, count, total = extract_http_duration_histogram(parsed, "/api/v1/")
    assert count == 10
    assert total == pytest.approx(0.12)
    assert buckets[0.01] == 5


def test_counter_rate():
    samples = [(0.0, 10.0), (10.0, 40.0)]
    assert counter_rate(samples) == pytest.approx(3.0)


def test_counter_rate_insufficient():
    assert counter_rate([(0.0, 10.0)]) is None
    assert counter_rate([]) is None
