"""THR-357 Fix 1: real startup sweep, synthetic persistent lifecycle evidence."""
from __future__ import annotations

import dataclasses
import inspect
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import patch

from scripts.test_agent_retirement import fixture
from runtime.daemon import __main__ as entry
from runtime.daemon.lifetime_lock import LockProof
from runtime.daemon.thread_delivery_release_service import _StartupThreadReleases
from runtime.infrastructure.thread_admission import ThreadAttempt, ThreadLifecycle
from runtime.infrastructure.thread_delivery_release import (
    STARTUP_ACTION, Budget, PreviewRequest, ReleaseCommand, ReleaseReader, ReleaseUnavailable,
    Selection, digest, delivery_view, native_read, preview,
)
from runtime.models import ThreadMessageKind, ThreadRecord
from runtime.orchestrator.team_move import DeferredTeamMove

PROVEN = LockProof(True, False, "earlier_lock_holders_excluded", None)


def seed(org: Any, name: str = "THR-recovery", *, settled: bool = True, quiescent: bool = True,
         producer: bool = True, prior: bool = True, extra: bool = False) -> Any:
    db = org.db
    db.insert_thread(ThreadRecord(id=name, subject="Synthetic startup recovery"))
    db.add_thread_participant(name, "worker", added_by="founder")
    db.record_conversational_arrival(thread_id=name, speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="Synthetic reply", recipients=["worker"])
    token = db.get_reply_delivery_state(name, "worker").queued_invocation_token
    admission = db._team_move_coordinator.threads
    owner = admission.reserve(token)
    owner.claim = db.peek_conversational_reply(token)
    owner.retry_schedule("host", ())
    owner.commit()
    inv = db.get_pending_invocation(token)
    store = admission.store
    state = owner.state
    def save(value: ThreadLifecycle) -> None:
        store.save_uncommitted(inv, value)
        db._conn.commit()
    attempt = state.attempts[-1].model_copy(update={"launched": True, "settled": settled, "quiescent": quiescent})
    state = state.model_copy(update={"attempts": (attempt,), "producer_settled": producer})
    if prior:
        save(state)
    if extra:
        state = state.model_copy(update={"attempts": state.attempts + (ThreadAttempt(
            attempt_id="extra", phase="initial", boundary="host", ordinal=0, possible_launch=True),)})
    state = state.model_copy(update={"failed_cleanup": True})
    save(state)
    # Simulated process exit: durable rows survive, in-memory owners do not.
    admission.live.clear()
    admission.controls.clear()
    return inv


def snapshot(db: Any, thread: str) -> dict[str, list[tuple]]:
    return {table: [tuple(r) for r in db.execute(f"SELECT * FROM {table} WHERE {key}=? ORDER BY 1", (thread,))]
            for table, key in (("thread_invocations", "thread_id"), ("audit_log", "task_id"),
                               ("thread_reply_delivery_state", "thread_id"))}


def sweep(org: Any, captured: _StartupThreadReleases) -> list[str]:
    # The same assertion runs against the actual pre-fix startup function too.
    kwargs = {"startup_releases": captured} if "startup_releases" in inspect.signature(entry._sweep_on_startup).parameters else {}
    return entry._sweep_on_startup(org.db, SimpleNamespace(enqueue=lambda *a, **k: None), org.slug, **kwargs)


class StartupReleaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_released_history_growth_is_thread_local(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            async with fixture(Path(scratch)/"org") as (org, _):
                released = []
                for n in range(3):
                    inv = seed(org, "THR-grown-" + str(n))
                    sweep(org, _StartupThreadReleases(org.db, org.slug, PROVEN))
                    self.assertEqual(org.db.execute(
                        "SELECT COUNT(*) FROM audit_log WHERE action=?", (STARTUP_ACTION,)
                    ).fetchone()[0], n + 1)
                    released.append(inv)
                fresh = seed(org, "THR-next-startup")
                # Real presentation writes AFTER each release committed. This
                # matches ordinary production histories, not fabricated evidence.
                for inv in released:
                    for n in range(4000):
                        self.assertTrue(org.db.set_thread_pinned_with_audit(
                            inv.thread_id, pinned=n % 2 == 0))
                    self.assertGreaterEqual(org.db.execute(
                        "SELECT COUNT(*) FROM audit_log WHERE task_id=?", (inv.thread_id,)
                    ).fetchone()[0], 4000)
                before = {inv.id: snapshot(org.db, inv.thread_id) for inv in released}
                org.db.insert_thread(ThreadRecord(id="THR-unrelated", subject="Unrelated"))
                org.db.add_thread_participant("THR-unrelated", "worker", added_by="founder")
                with self.subTest(boundary="unrelated_arrival"):
                    org.db.record_conversational_arrival(thread_id="THR-unrelated", speaker="founder",
                        kind=ThreadMessageKind.MESSAGE, body_markdown="Still deliver", recipients=["worker"])
                    self.assertIsNotNone(org.db.get_reply_delivery_state(
                        "THR-unrelated", "worker").queued_invocation_token)
                with self.subTest(boundary="subsequent_startup"):
                    sweep(org, _StartupThreadReleases(org.db, org.slug, PROVEN))
                    self.assertEqual(org.db.execute(
                        "SELECT COUNT(*) FROM audit_log WHERE action=?", (STARTUP_ACTION,)
                    ).fetchone()[0], 4)
                    self.assertIsNone(org.db.get_reply_delivery_state(
                        fresh.thread_id, "worker").running_invocation_token)
                for inv in released:
                    with self.subTest(boundary="exhausted_retained", thread=inv.thread_id):
                        from runtime.infrastructure.thread_delivery_release import pair_permission
                        with org.db._lock:
                            view = delivery_view(org.db, inv.thread_id)
                            self.assertIsNotNone(view)  # unavailable is never absence
                            self.assertFalse(view.released(inv.id))
                            self.assertFalse(pair_permission(view, inv.thread_id, "worker"))
                        self.assertEqual(snapshot(org.db, inv.thread_id), before[inv.id])

    async def test_previous_boot_release_and_next_reply_admits(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            async with fixture(Path(scratch)/"org") as (org, _):
                inv = seed(org)
                before = snapshot(org.db, inv.thread_id)
                captured = _StartupThreadReleases(org.db, org.slug, PROVEN)
                tokens = sweep(org, captured)
                current = org.db.get_reply_delivery_state(inv.thread_id, "worker")
                self.assertIsNone(current.running_invocation_token)
                self.assertIn(current.queued_invocation_token, tokens)
                release = org.db.execute("SELECT agent,payload FROM audit_log WHERE action=?", (STARTUP_ACTION,)).fetchall()
                self.assertEqual(len(release), 1)
                self.assertEqual(release[0]["agent"], "daemon")
                self.assertEqual(json.loads(release[0]["payload"])["principal"], "daemon")
                after = snapshot(org.db, inv.thread_id)
                self.assertEqual(after["audit_log"][:len(before["audit_log"])], before["audit_log"])
                raw = org.db._team_move_coordinator.threads.store.read_uncommitted(inv)
                self.assertTrue(raw.failed_cleanup and raw.unknown)
                with org.db._lock:
                    self.assertTrue(delivery_view(org.db, inv.thread_id).released(inv.id))
                owner = org.db._team_move_coordinator.threads.reserve(current.queued_invocation_token)
                self.assertIsNotNone(owner)
                owner.claim = org.db.peek_conversational_reply(current.queued_invocation_token)
                owner.retry_schedule("host", ())
                self.assertTrue(owner.commit())
                # No provider/process was launched; retain honest attempt state.
                owner.terminal(None)
                owner.finish(deferred=False, failed=False)
                original_count = org.db.execute("SELECT COUNT(*) FROM audit_log WHERE action=?", (STARTUP_ACTION,)).fetchone()[0]
                captured.run()
                self.assertEqual(original_count, org.db.execute("SELECT COUNT(*) FROM audit_log WHERE action=?", (STARTUP_ACTION,)).fetchone()[0])

    async def test_not_proof_cases_remain_byte_exact(self) -> None:
        cases = {
            "lock_unproven": ({}, dataclasses.replace(PROVEN, proven=False)),
            "transition_missing_pid": ({}, LockProof(False, True, "previous_pid_missing_or_invalid", None)),
            "transition_unclear_pid": ({}, LockProof(False, True, "previous_pid_probe_ambiguous", 123456)),
            "attempt_not_quiescent": ({"quiescent": False}, PROVEN),
            "attempt_not_settled": ({"settled": False, "quiescent": False}, PROVEN),
            "producer_not_settled": ({"producer": False}, PROVEN),
            "no_matching_prior": ({"prior": False}, PROVEN),
            "extra_uncertainty": ({"extra": True}, PROVEN),
        }
        for label, (options, proof) in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as scratch:
                async with fixture(Path(scratch)/"org") as (org, _):
                    inv = seed(org, **options)
                    captured = _StartupThreadReleases(org.db, org.slug, proof)
                    before = snapshot(org.db, inv.thread_id)
                    self.assertEqual(sweep(org, captured), [])
                    self.assertEqual(snapshot(org.db, inv.thread_id), before)

    async def test_current_boot_and_contradictory_evidence_stay_stuck(self) -> None:
        for case in ("new_invocation", "native_changed", "malformed_history", "late_history", "other_owner"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as scratch:
                async with fixture(Path(scratch)/"org") as (org, _):
                    if case == "new_invocation":
                        captured = _StartupThreadReleases(org.db, org.slug, PROVEN)
                        inv = seed(org)
                    else:
                        inv = seed(org)
                        captured = _StartupThreadReleases(org.db, org.slug, PROVEN)
                        if case == "native_changed":
                            org.db.execute("UPDATE thread_invocations SET session_id='contradiction' WHERE id=?", (inv.id,))
                        elif case == "malformed_history":
                            org.db.insert_audit_log_uncommitted(inv.thread_id, "worker", "thread_move_lifecycle", {"broken": True})
                        elif case == "late_history":
                            state = org.db._team_move_coordinator.threads.store.read_uncommitted(inv)
                            org.db.insert_audit_log_uncommitted(inv.thread_id, "worker", "thread_move_lifecycle", state.model_dump(mode="json"))
                        else:
                            token = org.db._mint_reply_invocation_uncommitted(inv.thread_id, "worker", 1)
                            other = org.db.get_pending_invocation(token)
                            state = ThreadLifecycle(org_slug=org.slug, invocation_id=other.id,
                                thread_id=inv.thread_id, agent_id="worker", session_id="sess-other",
                                preparation_id="prep-other", retry_initialized=False)
                            org.db._team_move_coordinator.threads.store.save_uncommitted(other, state)
                        org.db._conn.commit()
                    before = snapshot(org.db, inv.thread_id)
                    try:
                        tokens = sweep(org, captured)
                        self.assertEqual(tokens, [])
                    except (ValueError, DeferredTeamMove, ReleaseUnavailable):
                        # Existing strict recovery can refuse the entire startup
                        # on malformed input; it must not partially release it.
                        pass
                    self.assertEqual(snapshot(org.db, inv.thread_id), before)

    async def test_mid_release_failure_rolls_back_without_retry(self) -> None:
        for case in ("after_replacement", "timeout", "reader_error"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as scratch:
                async with fixture(Path(scratch)/"org") as (org, _):
                    inv = seed(org)
                    captured = _StartupThreadReleases(org.db, org.slug, PROVEN)
                    before = snapshot(org.db, inv.thread_id)
                    method = org.db._insert_thread_release_audit_uncommitted
                    def insert(*args: Any, **kwargs: Any) -> Any:
                        if args[3] == STARTUP_ACTION:
                            raise OSError("synthetic failure after replacement CAS/wake")
                        return method(*args, **kwargs)
                    if case == "after_replacement":
                        context = patch.object(org.db, "_insert_thread_release_audit_uncommitted", side_effect=insert)
                    elif case == "timeout":
                        context = patch("runtime.daemon.thread_delivery_release_service.Budget", side_effect=lambda: Budget(deadline=0))
                    else:
                        context = patch("runtime.infrastructure.thread_delivery_release.ReleaseReader.thread", side_effect=ValueError("synthetic reader"))
                    with context:
                        try:
                            sweep(org, captured)
                        except (ReleaseUnavailable, ValueError):
                            pass
                    self.assertEqual(snapshot(org.db, inv.thread_id), before)
                    self.assertFalse(org.db._conn.in_transaction)
                    captured.run()  # same boot never retries after failure
                    self.assertEqual(snapshot(org.db, inv.thread_id), before)

    async def test_human_replacement_default_and_serving_guard_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            async with fixture(Path(scratch)/"org") as (org, _):
                inv = seed(org)
                before = snapshot(org.db, inv.thread_id)
                with self.assertRaisesRegex(ReleaseUnavailable, "serving_owner_unavailable"):
                    org.db.release_exact_thread_delivery('{}')
                self.assertEqual(snapshot(org.db, inv.thread_id), before)
                # Exercise the changed shared leaf through its real human
                # default, without impersonating serving/founder authority.
                state = org.db._team_move_coordinator.threads.store.read_uncommitted(inv)
                selection = Selection(org_slug=org.slug, thread_id=inv.thread_id,
                    invocation_id=inv.id, agent_id=inv.agent_name, session_id=inv.session_id,
                    preparation_id=state.preparation_id, token_digest=digest(inv.invocation_token.encode()))
                with native_read(org.db, Budget()) as reader:
                    reader.execute("BEGIN IMMEDIATE").close()
                    try:
                        view = ReleaseReader(reader, org.slug)
                        view.inventory()
                        result = preview(view, PreviewRequest(version=1, operation_key="human-default",
                            mode="human_risk_override", selection=selection))
                        cmd = ReleaseCommand(**result["inputs"], confirmation={"confirmed": True,
                            "preview_digest": result["preview_digest"], "accepted_risks": tuple(result["accepted_risks"])})
                        evidence = view.thread(inv.thread_id)
                        original = next(row for row in evidence.invocations if row["id"] == inv.id)
                        replacement, wake_id, _ = org.db._replace_exact_running_reply_uncommitted(
                            reader, original, evidence, cmd, "2026-11-17T00:00:00+00:00")
                        source = reader.one("thread_invocations", "id=?", (inv.id,), "id")
                        wake = reader.one("audit_log", "id=?", (wake_id,), "id")
                        self.assertEqual(source["decline_reason"], "human_delivery_release")
                        self.assertEqual(json.loads(wake["payload"])["kind"], "human_delivery_release")
                        self.assertEqual(org.db.get_reply_delivery_state(inv.thread_id, inv.agent_name).queued_invocation_token,
                                         replacement["invocation_token"])
                    finally:
                        org.db._conn.rollback()
                self.assertEqual(snapshot(org.db, inv.thread_id), before)


if __name__ == "__main__":
    unittest.main()
