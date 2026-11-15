"""Standalone ASGI update checks (no pytest, lifespan, provider or subprocess).

--legacy-signal exercises the reviewed active/queue-only decoder for RED proof.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime.daemon import jobs_runner
from runtime.orchestrator.host_supervisor import AdmissionRequest, SessionOutcome
from runtime.platform.session_backend import CapabilityReport, TerminalReason
from scripts.test_agent_retirement import fixture, seed_future


async def check(legacy_signal: bool) -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="update-busy-") as scratch:
        async with fixture(Path(scratch) / "org") as (org, client):
            state = client._transport.app.state.daemon
            host = state.host_supervisor
            assert host is not None

            async def observe(name: str, busy: bool, unknown: bool = False) -> dict:
                changes = org.db._conn.total_changes
                response = await client.get("/api/v1/health", headers={"Authorization": ""})
                assert response.status_code == 200, response.text
                assert org.db._conn.total_changes == changes, "health must be read-only"
                body = response.json()
                value = body["update_busy"]
                assert set(value) == {"busy", "unknown", "counts"}
                assert set(value["counts"]) == {"host_active", "host_queued", "jobs_inflight",
                                                 "jobs_nonterminal", "invocations_nonterminal"}
                assert all(item is None or (type(item) is int and 0 <= item <= 1000)
                           for item in value["counts"].values())
                actual = value["busy"]
                if legacy_signal:
                    # The old shipping Swift decoder returned admission.active
                    # when active > 0 OR queue == 0 on otherwise healthy health.
                    admission = body.get("host_sessions", {}).get("admission", {})
                    actual = bool(admission.get("active") or admission.get("queue_depth"))
                if (actual, value["unknown"]) != (busy, unknown):
                    failures.append(name)
                    print(f"FAIL {name}: busy={actual}, unknown={value['unknown']}; expected {busy}/{unknown}")
                else:
                    print(f"PASS {name}: busy={actual}, unknown={unknown}")
                return body

            # Native capability is isolated at the existing backend observation
            # seam; real host admission/retry, registry, SQLite and route remain.
            with patch.object(host, "probe", return_value=CapabilityReport("synthetic", "1")):
                await observe("truly-idle", False)
                now = datetime.now(timezone.utc).isoformat()
                # A retained RUNNING row has unknown native liveness even when
                # the current daemon has no matching process registration.
                with org.db._lock:
                    org.db._conn.execute(
                        "INSERT INTO jobs (id,task_id,agent_name,title,script_text,interpreter,status,created_at) "
                        "VALUES ('JOB-update','TASK-update','worker','Synthetic','true','bash','running',?)", (now,))
                    org.db._conn.commit()
                body = await observe("running-durable-job-zero-host", True)
                assert body["host_sessions"]["admission"]["active"] == 0
                assert body["host_sessions"]["admission"]["queue_depth"] == 0
                assert body["update_busy"]["counts"]["jobs_nonterminal"] == 1
                with patch.dict(jobs_runner._INFLIGHT, {(org.db, "JOB-update"): object()}):
                    registered = await observe("registered-durable-job", True)
                    assert registered["update_busy"]["counts"]["jobs_inflight"] >= 1
                with org.db._lock:
                    org.db._conn.execute("UPDATE jobs SET status='completed' WHERE id='JOB-update'")
                    org.db._conn.commit()
                # Real retry loop releases its admission lease BEFORE entering
                # this deterministic sleep. Only executor/containment is fake.
                entered, release = threading.Event(), threading.Event()

                def backoff(_seconds: float) -> None:
                    entered.set()
                    assert release.wait(5), "route failed to release synthetic backoff"

                def attempt(request: AdmissionRequest, **kwargs: object) -> SessionOutcome:
                    retry = kwargs["attempt"] == 0
                    return SessionOutcome(request, TerminalReason.RATE_LIMITED if retry else TerminalReason.SUCCESS,
                                          int(kwargs["attempt"]), retry_worthy=retry)

                with patch.object(host, "_execute_attempt", side_effect=attempt), \
                     patch.object(host, "_max_retry_attempts", 1), \
                     patch.object(host, "_backoff_seconds", (1,)), patch.object(host, "_sleep", backoff):
                    org.sessions.set_active("TASK-update", "worker", "synthetic", org_slug=org.slug)
                    try:
                        running = asyncio.create_task(asyncio.to_thread(host.run,
                            AdmissionRequest(org.slug, "task", "TASK-update", "synthetic"),
                            launch_spec=None, launch_body=None))
                        try:
                            assert await asyncio.to_thread(entered.wait, 5), "retry did not reach backoff"
                            body = await observe("429-backoff-zero-host", True)
                            assert body["host_sessions"]["admission"]["active"] == 0
                            assert body["host_sessions"]["admission"]["queue_depth"] == 0
                            assert body["update_busy"]["counts"]["invocations_nonterminal"] == 1
                        finally:
                            release.set()
                            outcome = await running
                            assert outcome.attempt == 1
                    finally:
                        org.sessions.clear("TASK-update", "worker")
                if not legacy_signal:
                    # The real lifetime boundary is shared by thread/dream/wake/
                    # schedule runners and spans retries independently of leases.
                    with org._runner_lifetime() as admitted:
                        assert admitted
                        await observe("logical-runner-without-lease", True)
                    token = object()
                    assert org._acquire_native_task(token)
                    try:
                        await observe("native-task-owner-without-lease", True)
                    finally:
                        org._release_native_task(token)
                    with patch.object(org.sessions, "count_active", side_effect=OSError("synthetic")):
                        await observe("raising-logical-producer", True, True)
                    with patch("runtime.daemon.update_busy._count", side_effect=OSError("synthetic")):
                        await observe("unreadable-indexed-producer", True, True)
                    with patch.object(state, "host_supervisor", None):
                        await observe("unwired-host", True, True)
                    with patch.object(host, "health_snapshot", side_effect=OSError("synthetic")):
                        await observe("raising-host", True, True)
                    move = org.db._team_move_coordinator
                    for name, registry in (
                            ("retained-task-preparation", move._reservations),
                            ("retained-thread-control", move.threads.controls),
                            ("retained-producer-owner", move.producers.live),
                            ("retained-native-writer", move.producers.writers)):
                        with patch.dict(registry, {"synthetic": object()}):
                            await observe(name, True)
                    # Existing status-index readers cover durable producer
                    # records even without a current runner registration.
                    seed_future(org)
                    for table, row_id, live, terminal in (
                            ("dreams", "DRM-001", "running", "completed"),
                            ("work_hours", "WH-001", "running", "completed"),
                            ("schedules", "SCH-001", "firing", "fired")):
                        with org.db._lock:
                            org.db._conn.execute(f"UPDATE {table} SET status=? WHERE id=?", (live, row_id))
                            org.db._conn.commit()
                        await observe("retained-" + table, True)
                        with org.db._lock:
                            org.db._conn.execute(f"UPDATE {table} SET status=? WHERE id=?", (terminal, row_id))
                            org.db._conn.commit()
                    state.queue.enqueue(org.slug, "TASK-queued")
                    try:
                        await observe("queued-task-before-host-admission", True)
                    finally:
                        state.queue._queue.get_nowait()
                        state.queue._queue.task_done()
                    # A missing old index must fail closed, never fall back to
                    # scanning terminal history. Restore only this synthetic DB.
                    with org.db._lock:
                        org.db._conn.execute("DROP INDEX jobs_status_idx")
                    try:
                        await observe("missing-status-index", True, True)
                    finally:
                        with org.db._lock:
                            org.db._conn.execute("CREATE INDEX jobs_status_idx ON jobs(status)")
                            org.db._conn.commit()
                    # A grant during the host snapshot cannot hide its task
                    # binding from the later logical read.
                    original = host.health_snapshot

                    def arrival() -> dict:
                        snapshot = original()
                        org.sessions.set_active("TASK-arrival", "worker", "arriving", org_slug=org.slug)
                        return snapshot

                    try:
                        with patch.object(host, "health_snapshot", side_effect=arrival):
                            await observe("logical-arrival-after-host-read", True)
                    finally:
                        org.sessions.clear("TASK-arrival", "worker")
                    await observe("idle-after-terminal", False)
    print(f"{len(failures)} failed checks: {', '.join(failures)}")
    return int(bool(failures))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-signal", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(check(args.legacy_signal)))
