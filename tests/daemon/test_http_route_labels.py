"""Tests for route-template HTTP metric labels (THR-066 remediation Slice 1).

Proves the timing middleware labels HTTP latency by the matched FastAPI
route template (never the literal URL path), coalesces dynamic path values,
preserves method separation and the ``__all__`` aggregate, and uses bounded
``__unmatched__`` / ``__error__`` fallbacks that cannot admit IDs.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.daemon.app import metrics_timing_middleware
from runtime.daemon.metrics import (
    ERROR_LABEL,
    UNMATCHED_LABEL,
    MetricsRegistry,
    error_label,
    route_template_label,
)


class _RegistryHost:
    """Minimal app.state.daemon stand-in exposing only metrics_registry."""

    def __init__(self, registry: MetricsRegistry) -> None:
        self.metrics_registry = registry


def _make_app(registry: MetricsRegistry) -> FastAPI:
    app = FastAPI()
    app.state.daemon = _RegistryHost(registry)
    app.middleware("http")(metrics_timing_middleware)

    @app.get("/api/v1/orgs/{slug}/tasks/{task_id}/completion")
    def complete(slug: str, task_id: str):
        return {"slug": slug, "task_id": task_id}

    @app.post("/api/v1/orgs/{slug}/tasks/{task_id}/completion")
    def complete_post(slug: str, task_id: str):
        return {"slug": slug, "task_id": task_id}

    @app.get("/api/v1/orgs/{slug}/threads/{thread_id}")
    def thread(slug: str, thread_id: str):
        return {"thread_id": thread_id}

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/boom")
    def boom():
        raise RuntimeError("boom")

    return app


def _http_keys(registry: MetricsRegistry) -> set[str]:
    return set(registry.snapshot()["http"].keys())


# ---------------------------------------------------------------------------
# Pure label helpers
# ---------------------------------------------------------------------------

def test_route_template_label_uses_template() -> None:
    assert (
        route_template_label("POST", "/api/v1/orgs/{slug}/tasks/{task_id}/completion")
        == "POST /api/v1/orgs/{slug}/tasks/{task_id}/completion"
    )


def test_route_template_label_unmatched_fallback() -> None:
    assert route_template_label("GET", None) == f"GET {UNMATCHED_LABEL}"


def test_error_label_bounded() -> None:
    assert error_label("POST") == f"POST {ERROR_LABEL}"


def test_fallback_labels_never_admit_raw_paths() -> None:
    # Even if a caller tried to smuggle an ID-like path into the fallback,
    # the fallback label is fixed and contains no path material.
    assert UNMATCHED_LABEL not in "/api/v1/orgs"
    assert "TASK-1505" not in error_label("GET")
    assert "/" not in f"GET {UNMATCHED_LABEL}"


# ---------------------------------------------------------------------------
# Middleware behavior (end-to-end through the real middleware)
# ---------------------------------------------------------------------------

def test_dynamic_values_coalesce_to_one_label() -> None:
    registry = MetricsRegistry()
    client = TestClient(_make_app(registry))

    client.get("/api/v1/orgs/tourism-org/tasks/TASK-1505/completion")
    client.get("/api/v1/orgs/other-org/tasks/TASK-9999/completion")

    http = registry.snapshot()["http"]
    label = "GET /api/v1/orgs/{slug}/tasks/{task_id}/completion"
    assert label in http
    assert http[label]["count"] == 2
    # No raw-path key leaked in.
    for key in http:
        assert "TASK-1505" not in key
        assert "TASK-9999" not in key
        assert "tourism-org" not in key


def test_distinct_templates_remain_distinct() -> None:
    registry = MetricsRegistry()
    client = TestClient(_make_app(registry))

    client.get("/api/v1/orgs/a/tasks/T1/completion")
    client.get("/api/v1/orgs/a/threads/THR-1")

    http = registry.snapshot()["http"]
    assert "GET /api/v1/orgs/{slug}/tasks/{task_id}/completion" in http
    assert "GET /api/v1/orgs/{slug}/threads/{thread_id}" in http


def test_methods_remain_distinct() -> None:
    registry = MetricsRegistry()
    client = TestClient(_make_app(registry))

    client.get("/api/v1/orgs/a/tasks/T1/completion")
    client.post("/api/v1/orgs/a/tasks/T1/completion")

    http = registry.snapshot()["http"]
    assert "GET /api/v1/orgs/{slug}/tasks/{task_id}/completion" in http
    assert "POST /api/v1/orgs/{slug}/tasks/{task_id}/completion" in http


def test_unmatched_fallback_recorded_without_id() -> None:
    registry = MetricsRegistry()
    client = TestClient(_make_app(registry))

    # No route matches this path — must fall to __unmatched__, not the path.
    client.get("/api/v1/orgs/evil/tasks/TASK-9999/nonexistent")

    http = registry.snapshot()["http"]
    assert f"GET {UNMATCHED_LABEL}" in http
    for key in http:
        assert "TASK-9999" not in key
        assert "nonexistent" not in key


def test_error_fallback_recorded_and_reraised() -> None:
    registry = MetricsRegistry()
    client = TestClient(_make_app(registry), raise_server_exceptions=False)

    r = client.get("/boom")
    assert r.status_code == 500

    http = registry.snapshot()["http"]
    assert f"GET {ERROR_LABEL}" in http
    assert http[f"GET {ERROR_LABEL}"]["count"] == 1
    for key in http:
        assert "boom" not in key


def test_aggregate_bucket_spans_all_requests() -> None:
    registry = MetricsRegistry()
    client = TestClient(_make_app(registry))

    client.get("/api/v1/orgs/a/tasks/T1/completion")
    client.get("/api/v1/orgs/a/tasks/T2/completion")
    client.get("/health")

    http = registry.snapshot()["http"]
    per_route_sum = sum(
        h["count"] for k, h in http.items() if k != "__all__"
    )
    assert http["__all__"]["count"] == per_route_sum == 3
