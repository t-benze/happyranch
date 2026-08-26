"""Tests for MetricsStore offline maintenance primitives (TASK-5505).

Covers the architecture-neutral, store-local maintenance sequence carried
forward from the superseded PR #703 design and re-reviewed against current
main: strict-before prune at the caller-supplied cutoff, WAL checkpoint
(fail-closed on busy), ``PRAGMA integrity_check`` (must be exactly ``ok``
before compaction), controlled ``VACUUM``, post-vacuum integrity evidence,
and a deterministic bounded before/after telemetry report — with no labels,
IDs, slugs, or snapshot content.

There is deliberately NO route, gate, scheduler, or quiescence coupling: the
maintenance invocation is an offline/startup-only daemon one-shot.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from runtime.daemon.metrics_store import (
    MetricsMaintenanceError,
    MetricsStore,
    _RETENTION_DAYS,
)


def _now() -> datetime:
    return datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)


def _row(store: MetricsStore, iso: str, snap: dict) -> None:
    store.append_snapshot(iso, snap)


def _seed(
    tmp_path: Path,
    rows: list[tuple[str, dict]],
) -> MetricsStore:
    Path(tmp_path).mkdir(parents=True, exist_ok=True)
    store = MetricsStore(str(tmp_path / "metrics.db"))
    for iso, snap in rows:
        _row(store, iso, snap)
    return store


# ---------------------------------------------------------------------------
# Positive path
# ---------------------------------------------------------------------------

class TestMaintenanceSuccess:
    def test_maintenance_prunes_and_reports(self, tmp_path: Path) -> None:
        now = _now()
        old = (now - timedelta(days=60)).isoformat()
        recent = now.isoformat()
        store = _seed(tmp_path, [(old, {"n": 1}), (recent, {"n": 2})])

        cutoff = (now - timedelta(days=_RETENTION_DAYS)).isoformat()
        report = store.maintenance(cutoff)

        # The strictly-older row is gone; the recent row survives.
        rows = store.query()
        assert len(rows) == 1
        assert json.loads(rows[0]["snapshot_json"]) == {"n": 2}

        # Report shape: before/after, cutoff, pruned count, checkpoint,
        # post-vacuum checkpoint, integrity before/after, vacuum, duration.
        assert report["pruned_rows"] == 1
        assert report["cutoff"] == cutoff
        assert report["checkpoint"]["busy"] == 0
        assert set(report["checkpoint"].keys()) == {
            "busy", "log_frames", "checkpointed_frames",
        }
        # A second WAL checkpoint runs AFTER controlled VACUUM and its
        # result is recorded as explicit evidence in the report.
        assert report["post_vacuum_checkpoint"]["busy"] == 0
        assert set(report["post_vacuum_checkpoint"].keys()) == {
            "busy", "log_frames", "checkpointed_frames",
        }
        assert report["integrity_check_before_vacuum"] == "ok"
        assert report["integrity_check_after_vacuum"] == "ok"
        assert report["vacuum"] == "ok"
        assert report["duration_seconds"] >= 0

        # Before/after health aggregates.
        assert report["before"]["row_count"] == 2
        assert report["after"]["row_count"] == 1
        assert report["before"]["oldest_captured_at"] == old
        assert report["after"]["oldest_captured_at"] == recent
        assert report["after"]["newest_captured_at"] == recent
        for phase in ("before", "after"):
            assert report[phase]["db_bytes"] > 0
            assert report[phase]["wal_bytes"] is None or report[phase]["wal_bytes"] >= 0
            assert report[phase]["page_count"] >= 1
            assert report[phase]["freelist_count"] >= 0
            assert report[phase]["total_snapshot_bytes"] > 0
            assert report[phase]["route_label_count"] >= 0
        # The pruned row's payload is gone from the store.
        assert report["after"]["total_snapshot_bytes"] < report["before"]["total_snapshot_bytes"]

    def test_maintenance_exact_strict_before_cutoff(self, tmp_path: Path) -> None:
        """A row exactly AT the cutoff is retained; strictly older is pruned."""
        now = _now()
        cutoff = (now - timedelta(days=_RETENTION_DAYS)).isoformat()
        older = (now - timedelta(days=_RETENTION_DAYS, seconds=1)).isoformat()
        boundary = cutoff
        recent = now.isoformat()
        store = _seed(tmp_path, [
            (older, {"n": "older"}),
            (boundary, {"n": "boundary"}),
            (recent, {"n": "recent"}),
        ])

        report = store.maintenance(cutoff)
        assert report["pruned_rows"] == 1

        rows = store.query()
        remaining = {json.loads(r["snapshot_json"])["n"] for r in rows}
        assert remaining == {"boundary", "recent"}

    def test_maintenance_retains_exactly_30_day_constant(self) -> None:
        """The retention window constant is unchanged at exactly 30 days."""
        assert _RETENTION_DAYS == 30

    def test_maintenance_legacy_row_readable_and_unrewritten(
        self, tmp_path: Path
    ) -> None:
        """A legacy row (no format_version) survives maintenance byte-for-byte."""
        now = _now()
        recent = now.isoformat()
        legacy = {
            "uptime_seconds": 1.0,
            "http": {"GET /api/v1/orgs/tourism/tasks/TASK-1": {"count": 1, "p50": 0.1, "p95": 0.1, "max": 0.1}},
        }
        store = _seed(tmp_path, [(recent, legacy)])

        cutoff = (now - timedelta(days=_RETENTION_DAYS)).isoformat()
        store.maintenance(cutoff)

        rows = store.query()
        assert len(rows) == 1
        parsed = json.loads(rows[0]["snapshot_json"])
        assert parsed == legacy  # unchanged, no marker injected
        assert "GET /api/v1/orgs/tourism/tasks/TASK-1" in parsed["http"]

    def test_maintenance_on_empty_store(self, tmp_path: Path) -> None:
        store = MetricsStore(str(tmp_path / "metrics.db"))
        cutoff = _now().isoformat()
        report = store.maintenance(cutoff)
        assert report["pruned_rows"] == 0
        assert report["before"]["row_count"] == 0
        assert report["after"]["row_count"] == 0
        assert report["post_vacuum_checkpoint"]["busy"] == 0
        assert report["integrity_check_before_vacuum"] == "ok"
        assert report["integrity_check_after_vacuum"] == "ok"
        assert report["vacuum"] == "ok"


# ---------------------------------------------------------------------------
# Report telemetry: determinism + redaction
# ---------------------------------------------------------------------------

class TestMaintenanceTelemetry:
    def test_report_deterministic_given_same_store(self, tmp_path: Path) -> None:
        now = _now()
        rows = [
            ((now - timedelta(days=31)).isoformat(), {"n": 1}),
            (now.isoformat(), {"http": {"__all__": {"count": 1}, "GET /health": {"count": 1}}}),
        ]
        cutoff = (now - timedelta(days=_RETENTION_DAYS)).isoformat()

        r1 = _seed(tmp_path / "a", rows).maintenance(cutoff)
        r2 = _seed(tmp_path / "b", rows).maintenance(cutoff)

        # Duration is the only inherently non-deterministic field.
        r1.pop("duration_seconds")
        r2.pop("duration_seconds")
        assert r1 == r2

    def test_report_is_non_sensitive(self, tmp_path: Path) -> None:
        now = _now()
        store = _seed(tmp_path, [
            (now.isoformat(), {
                "http": {
                    "__all__": {"count": 1, "p50": None, "p95": None, "max": None},
                    "GET /api/v1/orgs/{slug}/tasks/{task_id}/completion": {"count": 1, "p50": 0.1, "p95": 0.1, "max": 0.1},
                },
            }),
        ])
        cutoff = (now - timedelta(days=_RETENTION_DAYS)).isoformat()
        report = store.maintenance(cutoff)

        blob = json.dumps(report, sort_keys=True)
        for sensitive in ("TASK-", "THR-", "slug", "tourism", "snapshot_json", "{slug}", "{task_id}"):
            assert sensitive not in blob
        # Values are bounded primitives or None.
        for v in report["before"].values():
            assert v is None or isinstance(v, (int, str))
        for v in report["after"].values():
            assert v is None or isinstance(v, (int, str))

    def test_report_captures_snapshot_bytes_and_route_label_cardinality(
        self, tmp_path: Path
    ) -> None:
        now = _now()
        snap = {
            "http": {
                "__all__": {"count": 1, "p50": None, "p95": None, "max": None},
                "GET /health": {"count": 1, "p50": 0.1, "p95": 0.1, "max": 0.1},
                "POST /api/v1/orgs/{slug}/tasks/{task_id}/completion": {"count": 1, "p50": 0.2, "p95": 0.2, "max": 0.2},
            },
        }
        store = _seed(tmp_path, [(now.isoformat(), snap)])
        cutoff = (now - timedelta(days=_RETENTION_DAYS)).isoformat()
        report = store.maintenance(cutoff)

        # Two distinct route labels (__all__ excluded).
        assert report["before"]["route_label_count"] == 2
        assert report["after"]["route_label_count"] == 2
        # Snapshot payload byte total reflects the stored JSON.
        assert report["before"]["total_snapshot_bytes"] == len(json.dumps(snap))

    def test_health_matches_report_aggregates(self, tmp_path: Path) -> None:
        now = _now()
        store = _seed(tmp_path, [(now.isoformat(), {"n": 1})])
        health = store.health()
        assert set(health.keys()) == {
            "row_count", "oldest_captured_at", "newest_captured_at",
            "total_snapshot_bytes", "route_label_count",
            "page_count", "freelist_count", "db_bytes", "wal_bytes",
        }
        assert health["row_count"] == 1
        assert health["oldest_captured_at"] == now.isoformat()
        assert health["db_bytes"] > 0
        assert health["page_count"] >= 1


# ---------------------------------------------------------------------------
# Fail-closed semantics
# ---------------------------------------------------------------------------

class TestMaintenanceFailClosed:
    def test_checkpoint_busy_fails_closed_before_vacuum(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        store = _seed(tmp_path, [(_now().isoformat(), {"n": 1})])
        monkeypatch.setattr(
            store, "_wal_checkpoint",
            lambda: {"busy": 1, "log_frames": 2, "checkpointed_frames": 0},
        )
        vacuum_called = []
        monkeypatch.setattr(store, "_vacuum", lambda: vacuum_called.append(True))

        with pytest.raises(MetricsMaintenanceError):
            store.maintenance(_now().isoformat())

        assert vacuum_called == []  # compaction never attempted
        # The store is untouched and fully queryable.
        assert len(store.query()) == 1

    def test_integrity_failure_fails_closed_before_vacuum(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        store = _seed(tmp_path, [(_now().isoformat(), {"n": 1})])
        monkeypatch.setattr(store, "_integrity_check", lambda: "database malformed")
        vacuum_called = []
        monkeypatch.setattr(store, "_vacuum", lambda: vacuum_called.append(True))

        with pytest.raises(MetricsMaintenanceError):
            store.maintenance(_now().isoformat())

        assert vacuum_called == []
        assert len(store.query()) == 1

    def test_integrity_failure_after_vacuum_fails_closed(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        store = _seed(tmp_path, [(_now().isoformat(), {"n": 1})])
        results = iter(["ok", "corrupted"])
        monkeypatch.setattr(store, "_integrity_check", lambda: next(results))

        with pytest.raises(MetricsMaintenanceError) as ei:
            store.maintenance(_now().isoformat())

        # Stable bounded classification only — never the raw integrity text.
        assert ei.value.code == "integrity-after-vacuum"
        # Rows still queryable after the failed post-vacuum integrity check.
        assert len(store.query()) == 1

    def test_post_vacuum_checkpoint_evidence_and_ordering(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """The post-VACUUM WAL checkpoint runs after VACUUM and BEFORE the
        final integrity evidence, and its result lands in the report."""
        store = _seed(tmp_path, [(_now().isoformat(), {"n": 1})])
        order: list[str] = []
        monkeypatch.setattr(
            store, "prune",
            lambda cutoff: (order.append("prune"), 0)[1],
        )
        monkeypatch.setattr(
            store, "_wal_checkpoint",
            lambda: (
                order.append("checkpoint"),
                {"busy": 0, "log_frames": 1, "checkpointed_frames": 1},
            )[1],
        )
        monkeypatch.setattr(
            store, "_integrity_check",
            lambda: (order.append("integrity"), "ok")[1],
        )
        monkeypatch.setattr(store, "_vacuum", lambda: order.append("vacuum"))

        report = store.maintenance(_now().isoformat())

        assert order == [
            "prune", "checkpoint", "integrity",
            "vacuum", "checkpoint", "integrity",
        ]
        assert report["post_vacuum_checkpoint"] == {
            "busy": 0, "log_frames": 1, "checkpointed_frames": 1,
        }

    def test_post_vacuum_checkpoint_busy_fails_closed(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A busy post-VACUUM checkpoint fails closed: no final integrity
        evidence, no success report, rows stay queryable, no file deletion."""
        now = _now()
        store = _seed(tmp_path, [
            ((now - timedelta(days=40)).isoformat(), {"n": "old"}),
            (now.isoformat(), {"n": "recent"}),
        ])
        calls: list[str] = []
        results = iter([
            {"busy": 0, "log_frames": 0, "checkpointed_frames": 0},
            {"busy": 1, "log_frames": 2, "checkpointed_frames": 0},
        ])
        monkeypatch.setattr(
            store, "_wal_checkpoint",
            lambda: (calls.append("checkpoint") or next(results)),
        )
        integrity_calls: list[str] = []
        monkeypatch.setattr(
            store, "_integrity_check",
            lambda: (integrity_calls.append("integrity") or "ok"),
        )

        with pytest.raises(MetricsMaintenanceError) as ei:
            store.maintenance(
                (now - timedelta(days=_RETENTION_DAYS)).isoformat()
            )

        assert ei.value.code == "post-vacuum-checkpoint-busy"
        # Both checkpoints ran (the post-vacuum one ran after VACUUM), but
        # the FINAL integrity evidence never ran → no success report exists.
        assert len(calls) == 2
        assert integrity_calls == ["integrity"]  # pre-vacuum only
        # Rows remain queryable and no metrics.db files were deleted.
        rows = store.query()
        assert {json.loads(r["snapshot_json"])["n"] for r in rows} == {"recent"}
        assert Path(store._db_path).exists()

    def test_post_vacuum_checkpoint_operational_error_fails_closed(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A post-VACUUM checkpoint operational error (e.g. database locked)
        fails closed at the entry seam; no success report is produced and the
        store stays queryable."""
        store = _seed(tmp_path, [(_now().isoformat(), {"n": 1})])
        calls: list[str] = []
        results = iter([
            {"busy": 0, "log_frames": 0, "checkpointed_frames": 0},
        ])

        def _second_checkpoint_locked():
            if not calls:
                calls.append("checkpoint")
                return next(results)
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(store, "_wal_checkpoint", _second_checkpoint_locked)
        integrity_calls: list[str] = []
        monkeypatch.setattr(
            store, "_integrity_check",
            lambda: (integrity_calls.append("integrity") or "ok"),
        )

        with pytest.raises(sqlite3.OperationalError):
            store.maintenance(_now().isoformat())

        # The final integrity evidence never ran after the failed post-vacuum
        # checkpoint; the exception is not concealed (entry seam → exit 1).
        assert len(calls) == 1
        assert integrity_calls == ["integrity"]
        rows = store.query()
        assert len(rows) == 1
        assert json.loads(rows[0]["snapshot_json"]) == {"n": 1}
        assert Path(store._db_path).exists()

    def test_vacuum_operational_failure_fails_closed(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        store = _seed(tmp_path, [(_now().isoformat(), {"n": 1})])

        def _locked():
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(store, "_vacuum", _locked)

        with pytest.raises(sqlite3.OperationalError):
            store.maintenance(_now().isoformat())

        # The exception is not concealed; pre-existing valid rows remain
        # queryable (SQLite guarantees: prune already committed, no partial
        # maintenance is claimed).
        rows = store.query()
        assert len(rows) == 1
        assert json.loads(rows[0]["snapshot_json"]) == {"n": 1}

    def test_maintenance_errors_carry_only_stable_codes(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Every maintenance failure carries a stable, non-sensitive
        classification code — never raw SQL/exception text (the entry seam
        logs codes only)."""
        now = _now()

        busy = _seed(tmp_path / "busy", [(now.isoformat(), {"n": 1})])
        monkeypatch.setattr(
            busy, "_wal_checkpoint",
            lambda: {"busy": 1, "log_frames": 2, "checkpointed_frames": 0},
        )
        with pytest.raises(MetricsMaintenanceError) as ei:
            busy.maintenance(now.isoformat())
        assert ei.value.code == "checkpoint-busy"
        assert "busy" in str(ei.value)  # code-only message, no raw rows

        bad_integrity = _seed(
            tmp_path / "integrity", [(now.isoformat(), {"n": 1})]
        )
        monkeypatch.setattr(
            bad_integrity, "_integrity_check",
            lambda: "SENSITIVE-" + "x" * 100_000,
        )
        with pytest.raises(MetricsMaintenanceError) as ei:
            bad_integrity.maintenance(now.isoformat())
        assert ei.value.code == "integrity-before-vacuum"
        assert "SENSITIVE-" not in str(ei.value)
        assert len(str(ei.value)) < 200

    def test_maintenance_never_deletes_sqlite_files(self, tmp_path: Path) -> None:
        """No filesystem-level deletion of metrics.db/-wal/-shm occurs."""
        now = _now()
        store = _seed(tmp_path, [
            ((now - timedelta(days=40)).isoformat(), {"n": 1}),
            (now.isoformat(), {"n": 2}),
        ])
        db = Path(store._db_path)
        before_files = {p.name for p in tmp_path.iterdir() if p.name.startswith("metrics.db")}

        store.maintenance((now - timedelta(days=_RETENTION_DAYS)).isoformat())
        store.close()

        after_files = {p.name for p in tmp_path.iterdir() if p.name.startswith("metrics.db")}
        # The main file survives untouched; the only sidecars that may appear
        # are SQLite's own -wal/-shm (never hand-created, never deleted).
        assert db.exists()
        assert after_files <= {"metrics.db", "metrics.db-wal", "metrics.db-shm"}
