"""Focused file-backed memory curation checks; no server or live org writes."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
from typing import Any, AsyncIterator, Callable
import unittest
from unittest.mock import patch

from runtime.daemon.routes import agents, dreams
from runtime.config import Settings
from runtime.infrastructure import learnings_store
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.infrastructure.database import Database
from runtime.infrastructure.learnings_store import LearningSummary, MemoryItem, MemoryStore
from runtime.models import DreamRecord, DreamStatus
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.org_config import MemoryCompactionConfig, load_org_config
from runtime.orchestrator.workspace_adapters import PersistentWorkspaceSetup, _get_workspace_lock


NOW = datetime(2026, 11, 16, tzinfo=timezone.utc)
OLD = NOW - timedelta(days=60)


class MemoryCompactionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        (self.root / "org").mkdir()
        (self.root / "org/config.yaml").write_text("memory_compaction:\n  enabled: true\n")
        self.store = MemoryStore(self.root / "workspaces/synthetic/memory")
        self.db = Database(self.root / "audit.db")
        self.addCleanup(self.db.close)
        self.org = SimpleNamespace(root=self.root, db=self.db, db_lock=asyncio.Lock())

    async def add(self, slug: str, *, at: datetime = OLD, **fields: object) -> str:
        body = agents.LearningAddBody(**{
            "slug": slug, "title": slug, "topic": "curation", "body": "Synthetic note.",
            **fields,
        })
        with patch.object(learnings_store, "datetime", wraps=datetime) as clock:
            clock.now.return_value = at
            result = await agents.add_learning("synthetic", "synthetic", body, self.org)
        return result["id"]

    async def update(self, mid: str, **fields: object) -> dict:
        old = self.store.read_entry(mid)
        body = agents.LearningUpdateBody(**{
            "slug": old.slug, "title": old.title, "topic": old.topic, "body": old.body,
            **fields,
        })
        with patch.object(learnings_store, "datetime", wraps=datetime) as clock:
            clock.now.return_value = NOW
            return await agents.update_learning("synthetic", "synthetic", mid, body, self.org)

    async def compact(self, *, at: datetime = NOW, **fields: object) -> dict:
        with patch.object(learnings_store, "datetime", wraps=datetime) as clock:
            clock.now.return_value = at
            return await agents.compact_memory("synthetic", "synthetic",
                                              agents.CompactBody(**fields), self.org)

    async def lifecycle(self, mid: str, state: str) -> dict:
        with patch.object(learnings_store, "datetime", wraps=datetime) as clock:
            clock.now.return_value = NOW
            return await agents.patch_lifecycle("synthetic", "synthetic", mid,
                agents.LifecyclePatchBody(lifecycle=state, reason="Synthetic curation"), self.org)

    def lifecycle_payloads(self) -> list[dict]:
        return [row["payload"] for row in self.db.get_audit_logs("AGENT-synthetic")
                if row["action"] == "memory_lifecycle_changed"]

    async def test_bootstrap_index_cannot_overwrite_compact_evictions(self) -> None:
        mid = await self.add("bootstrap-target", salience=0)
        workspace = self.store.root.parent
        lock = _get_workspace_lock(workspace)
        entered, captured, publish = threading.Event(), threading.Event(), threading.Event()
        loop_thread = threading.get_ident()
        offload, listing = asyncio.to_thread, MemoryStore.list_entries

        async def competing_worker(func: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
            def run() -> Any:
                entered.set()
                self.assertTrue(captured.wait(3), "bootstrap did not read summaries")
                # Only control scheduling: if bootstrap owns the real lock it
                # must publish first; otherwise let compact finish first, as in
                # the reviewer's stale-summary overwrite probe.
                available = lock.acquire(blocking=False)
                if available:
                    lock.release()
                else:
                    publish.set()
                try:
                    return func(*args, **kwargs)
                finally:
                    publish.set()
            return await offload(run)

        def capture_summaries(store: MemoryStore, **kwargs: Any) -> list[LearningSummary]:
            summaries = listing(store, **kwargs)
            if threading.get_ident() == loop_thread and store.root == self.store.root:
                self.assertEqual(summaries[0].lifecycle, "valid")
                self.assertTrue(self.org.db_lock.locked())
                captured.set()
                self.assertTrue(publish.wait(3), "competing writer did not reach publication")
            return summaries

        with patch.object(asyncio, "to_thread", competing_worker), \
                patch.object(MemoryStore, "list_entries", capture_summaries):
            task = asyncio.create_task(self.compact(dry_run=False))
            try:
                self.assertTrue(await offload(entered.wait, 3))
                PersistentWorkspaceSetup(Settings()).ensure(workspace, "synthetic")
                result = await task
            finally:
                captured.set()
                publish.set()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(result["evicted"], [mid])
        self.assertEqual(self.store.read_entry(mid).lifecycle, "evicted")
        self.assertIn(mid, [p["id"] for p in self.lifecycle_payloads()])
        self.assertNotIn(mid, (self.store.root / "_index.md").read_text())

    async def test_executor_bootstrap_compensation_cannot_restore_evicted_index(self) -> None:
        mid = await self.add("compensation-target", salience=0)
        workspace = self.store.root.parent
        agent_path = self.root / "org/agents/synthetic.md"
        agent_path.parent.mkdir()
        agent_path.write_text("---\nname: synthetic\nteam: engineering\nrole: worker\n"
                              "executor: claude\n---\nSynthetic role.\n")
        original = agent_path.read_bytes()
        self.org.settings = Settings()
        self.org.slug = "synthetic"
        self.org.teams_lock = asyncio.Lock()
        entered, release = threading.Event(), threading.Event()

        @asynccontextmanager
        async def authority(*args: Any, **kwargs: Any) -> AsyncIterator[SimpleNamespace]:
            yield SimpleNamespace(validation=nullcontext, canonical_change=nullcontext)

        def materialize(*args: Any, **kwargs: Any) -> list[str]:
            entered.set()
            self.assertTrue(release.wait(3), "materialization was not released")
            return []

        def fail_after_index(
            ctx: agents.ContextBuilder, target: Path, name: str, *args: Any, **kwargs: Any,
        ) -> None:
            PersistentWorkspaceSetup(ctx._settings).ensure(target, name)
            raise RuntimeError("synthetic failure after index publication")

        with patch.object(agents, "_consumer_writer_interval", authority), \
                patch.object(agents, "_executor_switch_materialize", materialize), \
                patch.object(agents.ContextBuilder, "ensure_workspace_ready", fail_after_index):
            switch = asyncio.create_task(agents.set_agent_executor(
                "synthetic", "synthetic", agents.SetExecutorBody(executor="codex"), self.org))
            compact = None
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                compact = asyncio.create_task(self.compact(dry_run=False))
                # If the switch excludes memory writers, let its real rollback
                # finish first. Otherwise reproduce eviction between the real
                # journal capture and the real bootstrap compensation.
                if not self.org.db_lock.locked():
                    await compact
                release.set()
                with self.assertRaises(agents.HTTPException) as error:
                    await switch
                self.assertEqual(error.exception.detail["code"], "executor_bootstrap_failed")
                result = await compact
            finally:
                release.set()
                await asyncio.gather(switch, *([compact] if compact else []), return_exceptions=True)
        self.assertEqual(result["evicted"], [mid])
        self.assertEqual(self.store.read_entry(mid).lifecycle, "evicted")
        self.assertIn(mid, [p["id"] for p in self.lifecycle_payloads()])
        self.assertNotIn(mid, (self.store.root / "_index.md").read_text())
        self.assertEqual(agent_path.read_bytes(), original)
        self.assertFalse(self.org.db_lock.locked())
        self.assertFalse(self.org.teams_lock.locked())

    def legacy_note(self, authored: str, *, updated: str = '"2026-11-16T00:00:00Z"') -> str:
        mid = self.store.next_id()
        self.store.path_for(mid, "legacy").write_text(
            f"---\nid: {mid}\nslug: legacy\ntitle: Legacy\ntopic: curation\n"
            f"authored_at: {authored}\nupdated_at: {updated}\nsalience: 0\n"
            "---\nSynthetic legacy note.\n")
        return mid

    async def test_yaml_timestamp_compaction_table(self) -> None:
        cases = (
            ("datetime", "2026-10-02T00:00:00Z", '"2026-11-16T00:00:00Z"', True),
            ("naive", "2026-10-02T00:00:00", '"2026-11-16T00:00:00Z"', True),
            ("date", "2026-10-02", '"2026-11-16T00:00:00Z"', True),
            ("offset-old", "2026-10-02T01:00:00+02:00", '"2026-11-16T00:00:00Z"', True),
            ("offset-young", "2026-10-02T00:00:00-02:00", '"2026-11-16T00:00:00Z"', False),
            ("integer-recent", "42", '"2026-11-16T00:00:00Z"', False),
            ("integer-old", "42", '"2026-01-01T00:00:00Z"', True),
            ("invalid-old", "unusable", '"2026-01-01T00:00:00Z"', True),
            ("missing-old", "null", '"2026-01-01T00:00:00Z"', True),
            ("neither-usable", "42", "unusable", False),
        )
        for label, authored, updated, eligible in cases:
            with self.subTest(case=label):
                sentinel = await self.add(f"eligible-{label}", salience=0)
                mid = self.legacy_note(authored, updated=updated)
                try:
                    before = self.store.path_for(mid, "legacy").read_bytes()
                    preview = await self.compact(dry_run=True)
                    candidate_ids = [c["id"] for c in preview["candidates"]]
                    self.assertEqual(mid in candidate_ids, eligible)
                    self.assertIn(sentinel, candidate_ids)
                    self.assertEqual(self.store.path_for(mid, "legacy").read_bytes(), before)
                    result = await self.compact(dry_run=False)
                    self.assertEqual(result["errors"], [])
                    self.assertEqual(mid in result["evicted"], eligible)
                    self.assertIn(sentinel, result["evicted"])
                    self.assertEqual(self.store.read_entry(mid).lifecycle, "evicted" if eligible else "valid")
                finally:
                    self.store.path_for(mid, "legacy").unlink()

    async def test_auto_trigger_add_audits_with_yaml_timestamps(self) -> None:
        (self.root / "org/config.yaml").write_text(
            "memory_compaction:\n  enabled: true\n  auto_trigger_entry_count: 1\n")
        for label, authored, eligible, lifecycle in (
                ("datetime", "2026-01-01T00:00:00Z", True, "valid"),
                ("date", "2026-01-01", True, "valid"),
                ("superseded", "2026-01-01", True, "superseded"),
                ("integer", "42", False, "valid")):
            with self.subTest(case=label):
                prior = self.legacy_note(authored)
                if lifecycle == "superseded":
                    item = self.store.read_entry(prior)
                    item.lifecycle = lifecycle
                    item.updated_at = OLD.isoformat()
                    self.store.path_for(prior, "legacy").write_text(self.store._serialize(item))
                try:
                    mid = await self.add(f"new-{label}", at=NOW)
                    self.assertTrue(self.store.path_for(mid, f"new-{label}").is_file())
                    self.assertEqual(self.store.read_entry(prior).lifecycle, "evicted" if eligible else "valid")
                    self.assertEqual(self.store.read_entry(mid).lifecycle, "valid")
                    added = [row["payload"] for row in self.db.get_audit_logs("AGENT-synthetic")
                             if row["action"] == "memory_added" and row["payload"]["id"] == mid]
                    self.assertEqual(added, [{"id": mid, "slug": f"new-{label}", "topic": "curation",
                                              "tags": [], "source_task": None}])
                    self.assertIn(mid, (self.store.root / "_index.md").read_text())
                    transitions = [row for row in self.db.get_audit_logs("AGENT-synthetic")
                                   if row["action"] == "memory_lifecycle_changed"
                                   and row["payload"]["id"] == prior]
                    self.assertEqual(len(transitions), int(eligible))
                    if eligible:
                        self.assertEqual(transitions[0]["task_id"], "AGENT-synthetic")
                        self.assertEqual(transitions[0]["agent"], "synthetic")
                        self.assertEqual(transitions[0]["payload"], {
                            "id": prior, "from_lifecycle": lifecycle, "to_lifecycle": "evicted",
                            "reason": "compaction (auto_trigger)", "source": "compaction"})
                finally:
                    self.store.path_for(prior, "legacy").unlink()

    async def test_auto_trigger_sweep_failure_does_not_prevent_add_audit(self) -> None:
        (self.root / "org/config.yaml").write_text(
            "memory_compaction:\n  enabled: true\n  auto_trigger_entry_count: 1\n")
        with patch.object(MemoryStore, "compact", side_effect=OSError("Synthetic sweep failure")):
            mid = await self.add("surviving-add", at=NOW)
        self.assertTrue(self.store.path_for(mid, "surviving-add").is_file())
        self.assertIn(mid, (self.store.root / "_index.md").read_text())
        added = [row["payload"] for row in self.db.get_audit_logs("AGENT-synthetic")
                 if row["action"] == "memory_added"]
        self.assertEqual(added, [{"id": mid, "slug": "surviving-add", "topic": "curation",
                                  "tags": [], "source_task": None}])

    async def test_auto_sweep_audits_eviction_before_later_write_failure(self) -> None:
        await self.assert_partial_auto_sweep_audited("later-eviction")

    async def test_auto_sweep_audits_evictions_before_index_failure(self) -> None:
        await self.assert_partial_auto_sweep_audited("sweep-index")

    async def assert_partial_auto_sweep_audited(self, failure: str) -> None:
        (self.root / "org/config.yaml").write_text("memory_compaction:\n  enabled: false\n")
        first = await self.add(f"first-{failure}", salience=0)
        second = await self.add(f"second-{failure}", salience=0)
        item = self.store.read_entry(first)
        item.lifecycle = "superseded"
        self.store.path_for(first, item.slug).write_text(self.store._serialize(item))
        (self.root / "org/config.yaml").write_text(
            "memory_compaction:\n  enabled: true\n  auto_trigger_entry_count: 1\n")
        atomic, regenerate = MemoryStore._atomic_write, MemoryStore.regenerate_index
        writer = AuditLogger.log_memory_lifecycle_changed
        index_calls = 0
        observed = []

        def write(store: MemoryStore, target: Path, content: str) -> None:
            if failure == "later-eviction" and target.name.startswith(second + "-"):
                raise OSError("Synthetic later eviction failure")
            atomic(store, target, content)

        def index(store: MemoryStore) -> None:
            nonlocal index_calls
            index_calls += 1
            if failure == "sweep-index" and index_calls == 1:
                raise OSError("Synthetic sweep index failure")
            regenerate(store)

        def audit(logger: AuditLogger, **fields: Any) -> None:
            observed.append((fields["id"], self.store.read_entry(fields["id"]).lifecycle,
                             self.org.db_lock.locked(), threading.get_ident()))
            writer(logger, **fields)

        with patch.object(MemoryStore, "_atomic_write", write), \
                patch.object(MemoryStore, "regenerate_index", index), \
                patch.object(AuditLogger, "log_memory_lifecycle_changed", audit), \
                self.assertLogs(level="WARNING"):
            mid = await self.add(f"surviving-{failure}", at=NOW)
        evicted = [first] if failure == "later-eviction" else [first, second]
        self.assertEqual(self.store.read_entry(first).lifecycle, "evicted")
        self.assertEqual(self.store.read_entry(second).lifecycle,
                         "valid" if failure == "later-eviction" else "evicted")
        self.assertEqual(self.store.read_entry(mid).lifecycle, "valid")
        index_text = (self.store.root / "_index.md").read_text()
        self.assertIn(mid, index_text)
        for retired in evicted:
            self.assertNotIn(retired, index_text)
        rows = self.db.get_audit_logs("AGENT-synthetic")
        self.assertEqual([r["payload"] for r in rows if r["action"] == "memory_added"
                          and r["payload"]["id"] == mid], [{
            "id": mid, "slug": f"surviving-{failure}", "topic": "curation",
            "tags": [], "source_task": None}])
        transitions = [r for r in rows if r["action"] == "memory_lifecycle_changed"
                       and r["payload"]["id"] in (first, second)]
        self.assertEqual([r["payload"] for r in transitions], [{
            "id": retired, "from_lifecycle": "superseded" if retired == first else "valid",
            "to_lifecycle": "evicted", "reason": "compaction (auto_trigger)",
            "source": "compaction"} for retired in evicted])
        self.assertTrue(all(r["task_id"] == "AGENT-synthetic" and r["agent"] == "synthetic"
                            for r in transitions))
        self.assertEqual([row[0] for row in observed], evicted)
        self.assertTrue(all(state == "evicted" and locked and thread != threading.get_ident()
                            for _, state, locked, thread in observed))

    async def test_auto_audit_failure_preserves_add_and_add_audit(self) -> None:
        prior = await self.add("audit-failure-target", salience=0)
        (self.root / "org/config.yaml").write_text(
            "memory_compaction:\n  enabled: true\n  auto_trigger_entry_count: 1\n")
        observed = []

        def fail_audit(logger: AuditLogger, **fields: Any) -> None:
            observed.append((fields["id"], self.store.read_entry(fields["id"]).lifecycle))
            raise OSError("Synthetic audit failure")

        with patch.object(AuditLogger, "log_memory_lifecycle_changed", fail_audit), \
                self.assertLogs(level="WARNING") as logs:
            mid = await self.add("audit-surviving-add", at=NOW)
        self.assertEqual(observed, [(prior, "evicted")])
        self.assertIn("Automatic memory compaction", "\n".join(logs.output))
        self.assertEqual(self.store.read_entry(prior).lifecycle, "evicted")
        self.assertEqual(self.store.read_entry(mid).lifecycle, "valid")
        added = [row["payload"]["id"] for row in self.db.get_audit_logs("AGENT-synthetic")
                 if row["action"] == "memory_added"]
        self.assertEqual(added, [prior, mid])
        self.assertIn(mid, (self.store.root / "_index.md").read_text())

    async def test_explicit_compaction_disable_settings(self) -> None:
        for label, config in (
                ("disabled", "enabled: false\n  auto_trigger_entry_count: 1"),
                ("no-auto", "enabled: true\n  auto_trigger_entry_count: 0")):
            with self.subTest(setting=label):
                prior = await self.add(f"disable-target-{label}", salience=0)
                (self.root / "org/config.yaml").write_text("memory_compaction:\n  " + config + "\n")
                mid = await self.add(f"disable-add-{label}", at=NOW)
                self.assertEqual(self.store.read_entry(prior).lifecycle, "valid")
                self.assertFalse([p for p in self.lifecycle_payloads() if p["id"] == prior])
                self.assertTrue(any(row["action"] == "memory_added" and row["payload"]["id"] == mid
                                    for row in self.db.get_audit_logs("AGENT-synthetic")))
                self.assertIn(prior, [c["id"] for c in (await self.compact())["candidates"]])
                if label == "disabled":
                    with self.assertRaises(agents.HTTPException) as error:
                        await self.compact(dry_run=False)
                    self.assertEqual(error.exception.status_code, 403)
                else:
                    self.assertIn(prior, (await self.compact(dry_run=False))["evicted"])

    async def test_compaction_defaults_without_section_and_partial_block(self) -> None:
        expected = MemoryCompactionConfig(enabled=True, auto_trigger_entry_count=150)
        self.assertEqual(MemoryCompactionConfig(), expected)
        config = self.root / "org/config.yaml"
        for text in (None, "{}\n", "memory_compaction: {}\n",
                     "memory_compaction:\n  salience_floor: 10\n"):
            with self.subTest(config=text):
                if text is None:
                    config.unlink(missing_ok=True)
                else:
                    config.write_text(text)
                self.assertEqual(load_org_config(OrgPaths(root=self.root)).memory_compaction, expected)
        config.write_text("{}\n")
        ids = self.seed(148)
        await self.add("below-default-threshold", at=NOW)
        self.assertTrue(all(self.store.read_entry(mid).lifecycle == "valid" for mid in ids))
        await self.add("at-default-threshold", at=NOW)
        self.assertEqual(sum(self.store.read_entry(mid).lifecycle == "evicted" for mid in ids), 25)
        self.assertEqual(len(self.lifecycle_payloads()), 25)

    async def test_dream_auto_compaction_audits_off_loop_and_survives_audit_failure(self) -> None:
        for failure in (False, True):
            label = "failure" if failure else "success"
            with self.subTest(audit_failure=failure):
                (self.root / "org/config.yaml").write_text("memory_compaction:\n  enabled: false\n")
                prior = await self.add(f"dream-target-{label}", salience=0)
                (self.root / "org/config.yaml").write_text(
                    "memory_compaction:\n  enabled: true\n  auto_trigger_entry_count: 1\n")
                did = f"DREAM-synthetic-{failure}"
                self.db.insert_dream(DreamRecord(id=did, agent_name="synthetic",
                    local_date="2026-11-17" if failure else "2026-11-16",
                    scheduled_for=NOW, window_end=NOW, status=DreamStatus.RUNNING))
                observed = []
                writer = AuditLogger.log_memory_lifecycle_changed
                sweep = MemoryStore.compact
                sweep_threads = []
                loop_thread = threading.get_ident()

                def compact(store: MemoryStore, **kwargs: Any) -> Any:
                    sweep_threads.append(threading.get_ident())
                    return sweep(store, **kwargs)

                def audit(logger: AuditLogger, **fields: Any) -> None:
                    observed.append((fields["id"], threading.get_ident(), self.org.db_lock.locked(),
                                     self.store.read_entry(fields["id"]).lifecycle))
                    if failure:
                        raise OSError("Synthetic dream audit failure")
                    writer(logger, **fields)

                body = dreams.DreamCompleteBody(summary="Synthetic reflection", learnings=[
                    dreams.DreamLearningBody(slug=f"reflection-{label}", title="Reflection",
                                             topic="curation", body="Synthetic note.")])
                with patch.object(AuditLogger, "log_memory_lifecycle_changed", audit), \
                        patch.object(MemoryStore, "compact", compact), \
                        patch.object(learnings_store, "datetime", wraps=datetime) as clock:
                    clock.now.return_value = NOW
                    if failure:
                        with self.assertLogs(level="WARNING"):
                            result = await dreams.complete_dream("synthetic", did, body, self.org, None)
                    else:
                        result = await dreams.complete_dream("synthetic", did, body, self.org, None)
                self.assertEqual(result["status"], "completed")
                self.assertEqual(len(sweep_threads), 1)
                self.assertNotEqual(sweep_threads[0], loop_thread)
                self.assertEqual([row[0] for row in observed], [prior])
                self.assertTrue(all(thread != loop_thread and locked and state == "evicted"
                                    for _, thread, locked, state in observed))
                self.assertEqual(self.store.read_entry(prior).lifecycle, "evicted")
                self.assertEqual(self.db.get_dream(did).new_learnings_count, 1)
                self.assertEqual(self.store.read_entry(f"reflection-{label}").lifecycle, "valid")
                payloads = [p for p in self.lifecycle_payloads() if p["id"] == prior]
                self.assertEqual(payloads, [] if failure else [{
                    "id": prior, "from_lifecycle": "valid", "to_lifecycle": "evicted",
                    "reason": "compaction (auto_trigger)", "source": "compaction"}])

    async def test_dream_cancellation_drains_entire_completion_under_lock(self) -> None:
        (self.root / "org/config.yaml").write_text("memory_compaction:\n  enabled: false\n")
        did = "DREAM-synthetic-cancel"
        self.db.insert_dream(DreamRecord(id=did, agent_name="synthetic", local_date="2026-11-16",
            scheduled_for=NOW, window_end=NOW, status=DreamStatus.RUNNING))
        body = dreams.DreamCompleteBody(summary="Synthetic completion", learnings=[
            dreams.DreamLearningBody(slug=f"reflection-{n}", title=f"Reflection {n}",
                                     topic="curation", body=f"Synthetic note {n}.") for n in (1, 2)],
            kb_candidates=[dreams.DreamKbCandidateBody(slug="candidate", title="Candidate",
                topic="curation", rationale="Synthetic rationale", body_markdown="Synthetic candidate.")])
        entered, release = threading.Event(), threading.Event()
        write, index = MemoryStore.write_entry, MemoryStore.regenerate_index
        transcript = dreams.DreamStore.write_transcript
        completed = AuditLogger.log_dream_completed
        observed = []
        loop = asyncio.get_running_loop()

        def record(step: str) -> None:
            observed.append((step, self.org.db_lock.locked(), threading.get_ident()))

        def reflection(store: MemoryStore, item: MemoryItem, agent: str) -> MemoryItem:
            record(item.slug)
            if item.slug == "reflection-1":
                entered.set()
                self.assertTrue(release.wait(3), "first reflection was not released")
            else:
                # Repeated request cancellation must also drain the admitted batch.
                loop.call_soon_threadsafe(task.cancel)
            return write(store, item, agent)

        def regenerate(store: MemoryStore) -> None:
            record("index")
            index(store)

        def write_transcript(store: dreams.DreamStore, **fields: Any) -> Path:
            record("transcript")
            return transcript(store, **fields)

        def audit(logger: AuditLogger, *args: Any, **fields: Any) -> None:
            record("completed-audit")
            completed(logger, *args, **fields)

        with patch.object(MemoryStore, "write_entry", reflection), \
                patch.object(MemoryStore, "regenerate_index", regenerate), \
                patch.object(dreams.DreamStore, "write_transcript", write_transcript), \
                patch.object(AuditLogger, "log_dream_completed", audit):
            task = asyncio.create_task(dreams.complete_dream("synthetic", did, body, self.org, None))
            try:
                async with asyncio.timeout(2):
                    while not entered.is_set():
                        await asyncio.sleep(0.001)
                task.cancel()
                await asyncio.sleep(0)
                self.assertTrue(self.org.db_lock.locked())
                self.assertFalse(task.done())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual([step for step, _, _ in observed],
                         ["reflection-1", "reflection-2", "index", "transcript", "completed-audit"])
        self.assertTrue(all(locked for _, locked, _ in observed))
        self.assertTrue(all(thread != threading.get_ident() for step, _, thread in observed
                            if step != "completed-audit"))
        self.assertFalse(self.org.db_lock.locked())
        for n in (1, 2):
            item = self.store.read_entry(f"reflection-{n}")
            self.assertEqual(item.body, f"Synthetic note {n}.\n")
            self.assertIn(item.id, (self.store.root / "_index.md").read_text())
        dream = self.db.get_dream(did)
        self.assertEqual(dream.status, DreamStatus.COMPLETED)
        self.assertEqual(dream.new_learnings_count, 2)
        self.assertEqual(dream.kb_candidate_count, 1)
        self.assertEqual(dream.summary, body.summary)
        self.assertIsNotNone(dream.ended_at)
        self.assertIn(body.summary, Path(dream.transcript_path).read_text())
        self.assertEqual([c.slug for c in self.db.list_dream_kb_candidates(dream_id=did)], ["candidate"])
        self.assertEqual([r["action"] for r in self.db.get_audit_logs(did)], ["dream_completed"])
        # A retry sees the terminal dream instead of conflicting with a partial note.
        with self.assertRaises(dreams.HTTPException) as retry:
            await dreams.complete_dream("synthetic", did, body, self.org, None)
        self.assertEqual(retry.exception.detail["code"], "dream_not_running")

    async def test_old_note_lowered_salience_evicts_and_restores(self) -> None:
        mid = await self.add("old-note")
        await self.update(mid, salience=5)
        paths = list(self.store.root.iterdir())
        before = {p.name: p.read_bytes() for p in paths}
        preview = await self.compact(dry_run=True)
        self.assertEqual([c["id"] for c in preview["candidates"]], [mid])
        self.assertEqual({p.name: p.read_bytes() for p in paths}, before)
        result = await self.compact(dry_run=False)
        self.assertEqual(result["evicted"], [mid])
        entry = self.store.read_entry(mid)
        self.assertEqual(entry.lifecycle, "evicted")
        self.assertEqual(entry.salience, 5)
        self.assertEqual(entry.authored_at, "2026-09-17T00:00:00Z")
        self.assertEqual(entry.updated_at, "2026-11-16T00:00:00Z")
        self.assertTrue(self.store.path_for(entry.id, entry.slug).exists())
        self.assertNotIn(mid, (self.store.root / "_index.md").read_text())
        restored = await self.lifecycle(mid, "valid")
        self.assertEqual(restored["previous_lifecycle"], "evicted")
        self.assertEqual(self.store.read_entry(mid).lifecycle, "valid")
        self.assertIn(mid, (self.store.root / "_index.md").read_text())
        self.assertEqual(self.lifecycle_payloads(), [
            {"id": mid, "from_lifecycle": "valid", "to_lifecycle": "evicted",
             "reason": "compaction (dry_run=False)", "source": "compaction"},
            {"id": mid, "from_lifecycle": "evicted", "to_lifecycle": "valid",
             "reason": "Synthetic curation", "source": "manual"},
        ])

    async def test_writer_salience_clamps_and_omission_preserves(self) -> None:
        mid = await self.add("bounded-note", salience=-20)
        self.assertEqual(self.store.read_entry(mid).salience, 0)
        self.assertEqual((await self.update(mid, salience=120))["salience"], 100)
        self.assertEqual(self.store.read_entry(mid).salience, 100)
        self.assertEqual((await self.update(mid, title="Content edit"))["salience"], 100)
        self.assertEqual((await self.update(mid, salience=0))["salience"], 0)
        default = await self.add("default-note")
        self.assertEqual(self.store.read_entry(default).salience, 50)
        self.assertNotIn("salience:", self.store.path_for(default, "default-note").read_text())
        direct = MemoryItem(id=mid, slug="bounded-note", title="Direct edit",
                            topic="curation", body="Synthetic.")
        self.store.update_entry(mid, direct, "synthetic")
        self.assertEqual(self.store.read_entry(mid).salience, 0)

    async def test_supersession_paths_observe_grace_and_evict(self) -> None:
        for path in ("lifecycle", "supersedes"):
            with self.subTest(path=path):
                mid = await self.add(f"prior-{path}")
                if path == "lifecycle":
                    await self.lifecycle(mid, "superseded")
                else:
                    await self.add("replacement", at=NOW, supersedes=mid)
                self.assertEqual(self.store.read_entry(mid).lifecycle, "superseded")
                self.assertNotIn(mid, (await self.compact(dry_run=False))["evicted"])
                self.assertNotIn(mid, (await self.compact(
                    at=NOW + timedelta(days=7) - timedelta(seconds=1), dry_run=False))["evicted"])
                result = await self.compact(at=NOW + timedelta(days=7), dry_run=False)
                self.assertIn(mid, result["evicted"])
                self.assertEqual(self.store.read_entry(mid).lifecycle, "evicted")
                expected_source = "manual" if path == "lifecycle" else "supersedes"
                transitions = [p for p in self.lifecycle_payloads() if p["id"] == mid]
                self.assertEqual(transitions, [
                    {"id": mid, "from_lifecycle": "valid", "to_lifecycle": "superseded",
                     "reason": "Synthetic curation" if path == "lifecycle" else "superseded by MEM-003",
                     "source": expected_source},
                    {"id": mid, "from_lifecycle": "superseded", "to_lifecycle": "evicted",
                     "reason": "compaction (dry_run=False)", "source": "compaction"},
                ])

    async def test_protections_survive_both_eviction_paths(self) -> None:
        cases = ("promoted", "directive", "team", "org", "related", "body", "legacy-body")
        for state in ("valid", "superseded"):
            for kind in cases + (("supersedes",) if state == "valid" else ()):
                with self.subTest(state=state, protection=kind):
                    mid = await self.add(f"protected-{state}-{kind}", salience=0)
                    item = self.store.read_entry(mid)
                    item.lifecycle = state
                    item.updated_at = item.authored_at
                    if kind == "promoted":
                        item.promoted_to = "shared-rule"
                    elif kind == "directive":
                        item.provenance = "directive"
                    elif kind in ("team", "org"):
                        item.scope = kind
                    else:
                        if kind == "supersedes":
                            reference = {"supersedes": mid}
                        elif kind == "related":
                            reference = {"related_to": [mid]}
                        else:
                            reference = {"body": mid if kind == "body" else mid.replace("MEM-", "LRN-")}
                        await self.add(f"reference-{state}-{kind}", **reference)
                    self.store.path_for(mid, item.slug).write_text(self.store._serialize(item))
                    sentinel = await self.add(f"eligible-{state}-{kind}", salience=0)
                    # Keep cap exhaustion independent of this protection table,
                    # including when a deliberate regression admits earlier rows.
                    result = await self.compact(dry_run=False, max_evictions_per_run=1000)
                    self.assertIn(sentinel, result["evicted"])
                    self.assertNotIn(mid, result["evicted"])
                    self.assertEqual(self.store.read_entry(mid).lifecycle, state)

    async def test_stale_boundary_and_cap(self) -> None:
        old = await self.add("old-low", salience=10, at=NOW - timedelta(days=45))
        young = await self.add("young-low", salience=0,
                               at=NOW - timedelta(days=45) + timedelta(seconds=1))
        ordinary = await self.add("ordinary")
        second = await self.add("second-low", salience=10)
        result = await self.compact(dry_run=False, max_evictions_per_run=1)
        self.assertEqual(result["evicted"], [old])
        self.assertIn({"id": second, "reason": "cap_reached"}, result["skipped"])
        self.assertEqual(self.store.read_entry(young).lifecycle, "valid")
        self.assertEqual(self.store.read_entry(ordinary).lifecycle, "valid")
        self.assertEqual((await self.compact(dry_run=False))["evicted"], [second])
        self.assertEqual((await self.compact(dry_run=False))["evicted"], [])

    def seed(self, count: int = 300) -> list[str]:
        ids = []
        for number in range(1, count + 1):
            mid = f"MEM-{number:03d}"
            item = MemoryItem(id=mid, slug=f"note-{number}", title=mid, topic="curation",
                              body="Synthetic note.", salience=0,
                              authored_at=OLD.isoformat(), updated_at=OLD.isoformat())
            self.store.path_for(mid, item.slug).write_text(self.store._serialize(item))
            ids.append(mid)
        return ids

    async def test_compaction_entry_parses_are_linear(self) -> None:
        ids = self.seed()
        for dry_run, budget in ((True, 300), (False, 625)):
            with self.subTest(dry_run=dry_run):
                parses = 0
                parse = self.store._parse

                def counted(text: str) -> MemoryItem:
                    nonlocal parses
                    parses += 1
                    self.assertLessEqual(parses, budget, "compaction exceeded linear entry-parse budget")
                    return parse(text)

                with patch.object(self.store, "_parse", side_effect=counted):
                    result = self.store.compact(dry_run=dry_run, now=NOW)
                self.assertEqual(parses, budget)
                self.assertEqual([c.id for c in result.candidates], ids[:25])
                self.assertEqual(result.skipped, [{"id": mid, "reason": "cap_reached"} for mid in ids[25:]])
                self.assertEqual(result.errors, [])
                self.assertEqual(result.evicted, [] if dry_run else ids[:25])

    async def test_reference_sources_aliases_and_token_boundaries(self) -> None:
        # Each case has one target and one source; expected decisions are
        # independent of the implementation's reference index.
        cases = (
            ("related-alias", "valid", "valid", {"related_to": ["LRN-001"]}, True),
            ("supersedes-valid", "valid", "valid", {"supersedes": "LRN-001"}, True),
            ("supersedes-retired", "superseded", "valid", {"supersedes": "MEM-001"}, False),
            ("related-retired", "superseded", "superseded", {"related_to": ["MEM-001"]}, True),
            ("body-retired", "superseded", "superseded", {"body": "(LRN-001)."}, True),
            ("evicted-source", "valid", "evicted", {"related_to": ["MEM-001"], "body": "MEM-001"}, False),
            ("body-boundary", "valid", "valid", {"body": "MEM-001-MEM-002"}, True),
            ("body-long-number", "valid", "valid", {"body": "MEM-0010"}, False),
            ("body-word", "valid", "valid", {"body": "xMEM-001 MEM-001_ éMEM-001"}, False),
            ("self-reference", "valid", "valid", {"body": "MEM-001"}, True),
            ("historical-id", "valid", "valid", {"body": "(old.id)"}, True),
        )
        for label, lifecycle, source_lifecycle, refs, protected in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as folder:
                store = MemoryStore(Path(folder))
                mid = "old.id" if label == "historical-id" else "MEM-001"
                target = MemoryItem(id=mid, slug="target", title="Target", topic="curation",
                                    body="Synthetic.", salience=0, lifecycle=lifecycle,
                                    authored_at=OLD.isoformat(), updated_at=OLD.isoformat())
                source = MemoryItem(id="MEM-002", slug="source", title="Source", topic="curation",
                                    body="Synthetic.", lifecycle=source_lifecycle)
                if label == "self-reference":
                    target.body = refs["body"]
                else:
                    for key, value in refs.items():
                        setattr(source, key, value)
                # Filenames remain resolvable even for historical frontmatter IDs.
                store.path_for("MEM-001", "target").write_text(store._serialize(target))
                store.path_for(source.id, source.slug).write_text(store._serialize(source))
                preview = store.compact(dry_run=True, now=NOW)
                self.assertEqual(preview.skipped[0] if protected else preview.candidates[0].id,
                                 {"id": mid, "reason": "referenced_by_others"} if protected else mid)
                applied = store.compact(dry_run=False, now=NOW)
                self.assertEqual(applied.evicted, [] if protected else [mid])

    async def test_apply_reads_fresh_candidate_and_references(self) -> None:
        for change, reason in (("directive", "became_protected"),
                               ("salience", "no_longer_eligible"),
                               ("self-reference", "became_protected"),
                               ("missing", "not_found_on_apply"),
                               ("malformed", "parse_error_on_apply")):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as folder:
                store = MemoryStore(Path(folder))
                item = MemoryItem(id="MEM-001", slug="target", title="Target", topic="curation",
                                  body="Synthetic.", salience=0, authored_at=OLD.isoformat())
                path = store.path_for(item.id, item.slug)
                path.write_text(store._serialize(item))
                find = store._find_by_id

                def changed(mid: str) -> Path | None:
                    if change == "missing":
                        path.unlink()
                    elif change == "malformed":
                        path.write_text("Malformed synthetic note.")
                    else:
                        if change == "directive":
                            item.provenance = "directive"
                        elif change == "salience":
                            item.salience = 100
                        else:
                            item.body = item.id
                        path.write_text(store._serialize(item))
                    return find(mid)

                with patch.object(store, "_find_by_id", side_effect=changed):
                    result = store.compact(dry_run=False, now=NOW)
                self.assertEqual([c.id for c in result.candidates], [item.id])
                self.assertEqual(result.evicted, [])
                self.assertEqual(result.skipped, [{"id": item.id, "reason": reason}])
                self.assertEqual(result.errors, [f"{item.id}: {reason}"]
                                 if change in ("missing", "malformed") else [])

    async def test_apply_tracks_reference_source_changes(self) -> None:
        for change in ("evicted", "missing", "malformed", "different-filename"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as folder:
                store = MemoryStore(Path(folder))
                first = MemoryItem(id="MEM-001", slug="first", title="First", topic="curation",
                                   body="Synthetic.", salience=0, supersedes="LRN-002",
                                   authored_at=OLD.isoformat())
                first_path = store.path_for(first.id, first.slug)
                if change == "different-filename":
                    first.slug = "other-name"
                second = MemoryItem(id="MEM-002", slug="second", title="Second", topic="curation",
                                    body="Synthetic.", salience=0, lifecycle="superseded",
                                    authored_at=OLD.isoformat(), updated_at=OLD.isoformat())
                first_path.write_text(store._serialize(first))
                store.path_for(second.id, second.slug).write_text(store._serialize(second))
                find = store._find_by_id

                def changed(mid: str) -> Path | None:
                    if mid == first.id and change == "missing":
                        first_path.unlink()
                    elif mid == first.id and change == "malformed":
                        first_path.write_text("Malformed synthetic source.")
                    elif mid == second.id:
                        second.lifecycle = "valid"
                        store.path_for(second.id, second.slug).write_text(store._serialize(second))
                    return find(mid)

                with patch.object(store, "_find_by_id", side_effect=changed):
                    result = store.compact(dry_run=False, now=NOW)
                if change == "evicted":
                    self.assertEqual(result.evicted, [first.id, second.id])
                    self.assertEqual(result.skipped, [])
                elif change == "different-filename":
                    # The old file remains non-evicted when the persisted slug
                    # points elsewhere; its references must still protect.
                    self.assertEqual(result.evicted, [first.id])
                    self.assertEqual(result.skipped, [{"id": second.id, "reason": "became_protected"}])
                else:
                    self.assertEqual(result.evicted, [second.id])
                    reason = "not_found_on_apply" if change == "missing" else "parse_error_on_apply"
                    self.assertEqual(result.skipped, [{"id": first.id, "reason": reason}])

    async def test_routes_allow_loop_progress_during_sweep(self) -> None:
        self.seed()
        for route in ("dry-run", "apply", "auto-add"):
            with self.subTest(route=route):
                if route == "auto-add":
                    (self.root / "org/config.yaml").write_text(
                        "memory_compaction:\n  enabled: true\n  auto_trigger_entry_count: 1\n")
                entered, progress = threading.Event(), threading.Event()
                observed = []
                compact = MemoryStore.compact

                def sweep(store: MemoryStore, **kwargs: object) -> object:
                    entered.set()
                    observed.append((progress.wait(0.5), threading.get_ident()))
                    self.assertTrue(observed[-1][0], "event loop stalled during compaction")
                    return compact(store, **kwargs)

                async def heartbeat() -> None:
                    while not entered.is_set():
                        await asyncio.sleep(0.001)
                    self.assertTrue(self.org.db_lock.locked())
                    progress.set()

                observer = asyncio.create_task(heartbeat())
                try:
                    with patch.object(MemoryStore, "compact", autospec=True, side_effect=sweep):
                        if route == "auto-add":
                            await self.add("loop-add", at=NOW)
                        else:
                            await self.compact(dry_run=route == "dry-run")
                    await observer
                    self.assertEqual(len(observed), 1)
                    self.assertTrue(observed[0][0], "event loop stalled during compaction")
                    self.assertNotEqual(observed[0][1], threading.get_ident())
                finally:
                    observer.cancel()
                    await asyncio.gather(observer, return_exceptions=True)

    async def test_route_cancellation_drains_writes_and_audits_under_lock(self) -> None:
        for route in ("apply", "auto-add"):
            with self.subTest(route=route):
                mid = await self.add(f"cancel-target-{route}", salience=0)
                if route == "auto-add":
                    (self.root / "org/config.yaml").write_text(
                        "memory_compaction:\n  enabled: true\n  auto_trigger_entry_count: 1\n")
                entered, release = threading.Event(), threading.Event()
                compact = MemoryStore.compact

                def sweep(store: MemoryStore, **kwargs: object) -> object:
                    entered.set()
                    release.wait(1)
                    return compact(store, **kwargs)

                with patch.object(MemoryStore, "compact", autospec=True, side_effect=sweep):
                    task = asyncio.create_task(self.compact(dry_run=False) if route == "apply"
                                               else self.add("cancelled-add", at=NOW))
                    try:
                        async with asyncio.timeout(2):
                            while not entered.is_set():
                                await asyncio.sleep(0.001)
                        task.cancel()
                        await asyncio.sleep(0)
                        self.assertTrue(self.org.db_lock.locked())
                        self.assertFalse(task.done())
                        release.set()
                        with self.assertRaises(asyncio.CancelledError):
                            await task
                    finally:
                        release.set()
                        await asyncio.gather(task, return_exceptions=True)
                self.assertFalse(self.org.db_lock.locked())
                self.assertEqual(self.store.read_entry(mid).lifecycle, "evicted")
                if route == "apply":
                    self.assertIn(mid, [p["id"] for p in self.lifecycle_payloads()])
                else:
                    adds = [r["payload"]["slug"] for r in self.db.get_audit_logs("AGENT-synthetic")
                            if r["action"] == "memory_added"]
                    self.assertIn("cancelled-add", adds)


if __name__ == "__main__":
    unittest.main()
