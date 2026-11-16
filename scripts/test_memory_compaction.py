"""Focused file-backed memory curation checks; no server or live org writes."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from runtime.daemon.routes import agents
from runtime.infrastructure import learnings_store
from runtime.infrastructure.database import Database
from runtime.infrastructure.learnings_store import MemoryItem, MemoryStore


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
        for label, authored, eligible in (("datetime", "2026-01-01T00:00:00Z", True),
                                         ("date", "2026-01-01", True),
                                         ("integer", "42", False)):
            with self.subTest(case=label):
                prior = self.legacy_note(authored)
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


if __name__ == "__main__":
    unittest.main()
