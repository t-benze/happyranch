"""Focused memory rendering checks outside the retired Python unit selection."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from runtime.infrastructure.audit_logger import AuditLogger
from runtime.infrastructure.database import Database
from runtime.infrastructure.learnings_store import MemoryItem, MemoryStore


class MemoryDigestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.store = MemoryStore(self.root / 'memory')

    def entry(self, number: int, title: str, **fields: object) -> None:
        item = MemoryItem(**{'id': f'MEM-{number:03d}', 'slug': f'note-{number}',
                             'title': title, 'topic': 'misc', 'body': '', **fields})
        self.store.path_for(item.id, item.slug).write_text(self.store._serialize(item))

    def test_term_relevance_precedes_recency_salience_and_lineage(self) -> None:
        self.entry(1, 'memory search', body='to be a an', salience=0, updated_at='2020-01-01T00:00:00Z')
        self.entry(2, 'memory', salience=100, source_task='TASK-ancestor',
                   updated_at='2026-11-12T00:00:00Z')
        self.entry(3, 'Mac release', salience=100, updated_at='2026-11-13T00:00:00Z')
        self.entry(4, 'the and with', salience=100, updated_at='2026-11-14T00:00:00Z')
        rendered = self.store.render_memory_digest('the memory search and with', budget=5000,
                                                   ancestor_task_ids={'TASK-ancestor'})
        self.assertEqual(rendered.digest_ids, ('MEM-001', 'MEM-002', 'MEM-004', 'MEM-003'))
        self.assertNotIn('salience', rendered.text)
        self.assertIn('(experiential)', rendered.text)
        for brief in ('', 'the and with', 'to be', 'a an'):
            with self.subTest(brief=brief):
                self.assertEqual(self.store.render_memory_digest(brief, budget=5000).digest_ids,
                                 ('MEM-004', 'MEM-003', 'MEM-002', 'MEM-001'))

    def test_recency_uses_instant_with_invalid_dates_last(self) -> None:
        self.entry(1, 'Alpha memory', updated_at='2026-11-14T10:00:00+08:00')
        self.entry(2, 'Zulu memory', updated_at='2026-11-14T03:00:00Z')
        self.entry(3, 'Beta memory', updated_at=None)
        self.entry(4, 'Delta memory', updated_at='invalid')
        self.entry(5, 'Echo memory', updated_at='2026-11-14T02:30:00')
        self.assertEqual(self.store.render_memory_digest('memory', budget=5000).digest_ids,
                         ('MEM-002', 'MEM-005', 'MEM-001', 'MEM-003', 'MEM-004'))

    def test_search_scoring_and_short_query_are_preserved(self) -> None:
        self.entry(1, 'memory search')
        self.entry(2, 'memory', topic='search', tags=['memory'],
                   body='memory search alpha beta gamma')
        self.entry(3, 'Go')
        self.assertEqual([(hit.id, hit.score) for hit in self.store.search(' MEMORY search ')],
                         [('MEM-001', 69), ('MEM-002', 41)])
        self.assertEqual([(hit.id, hit.score) for hit in self.store.search('go')],
                         [('MEM-003', 57)])
        self.assertEqual([(hit.id, hit.score) for hit in
                          self.store.search('memory search alpha beta gamma')],
                         [('MEM-002', 49), ('MEM-001', 29)])
        self.assertEqual(self.store.search('  '), [])
        self.assertEqual(len(self.store.search('memory', limit=1)), 1)

    def test_budget_filters_and_read_only_render(self) -> None:
        self.entry(1, 'memory')
        self.entry(2, 'memory evicted', lifecycle='evicted')
        self.entry(3, 'memory superseded', lifecycle='superseded')
        self.entry(4, 'memory promoted', promoted_to='kb-note')
        self.store.regenerate_index()
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns)
                  for p in self.store.root.iterdir()}
        full = self.store.render_memory_digest('memory')
        self.assertEqual(full.digest_ids, ('MEM-001',))
        self.assertEqual(self.store.build_memory_digest('memory'), full.text)
        self.assertIsNone(self.store.render_memory_digest('memory', budget=0).text)
        self.assertIsNone(self.store.render_memory_digest('memory', budget=1).text)
        self.assertEqual(self.store.render_memory_digest('memory', budget=len(full.text)).text,
                         full.text)
        after = {p.name: (p.read_bytes(), p.stat().st_mtime_ns)
                 for p in self.store.root.iterdir()}
        self.assertEqual(before, after)
        self.assertIn('salience 50', (self.store.root / '_index.md').read_text())

    def test_directives_keep_full_body_lane_and_relevance_order(self) -> None:
        self.entry(1, 'unrelated directive', provenance='directive', body='Policy.',
                   updated_at='2026-11-14T00:00:00Z')
        self.entry(2, 'memory directive', provenance='directive', body='Policy.',
                   updated_at='2020-01-01T00:00:00Z')
        self.entry(3, 'memory search')
        rendered = self.store.render_memory_digest('memory search', budget=5000)
        self.assertEqual(rendered.full_body_ids, ('MEM-002', 'MEM-001'))
        self.assertEqual(rendered.pointer_ids, ('MEM-003',))
        self.assertLess(rendered.text.index('MEM-002'), rendered.text.index('MEM-001'))
        self.assertNotIn('salience', rendered.text)

    def test_impression_payload_bytes_types_and_render_order(self) -> None:
        self.entry(1, 'A memory directive', provenance='directive',
                   body='Read the policy; mentioned MEM-999 is not exposure. ' * 20)
        self.entry(2, 'Z memory pointer')
        db = Database(self.root / 'audit.db')
        self.addCleanup(db.close)
        logger = AuditLogger(db)
        for budget, full_ids, pointer_ids in ((5000, ['MEM-001'], ['MEM-002']),
                                               (400, [], ['MEM-001', 'MEM-002'])):
            with self.subTest(budget=budget):
                rendered = self.store.render_memory_digest('memory', budget=budget)
                self.assertEqual(list(rendered.full_body_ids), full_ids)
                self.assertEqual(list(rendered.pointer_ids), pointer_ids)
                self.assertEqual(list(rendered.digest_ids), full_ids + pointer_ids)
                logger.log_memory_digest_impression(agent='synthetic-agent', task_id='TASK-test',
                    session_id='synthetic-session', digest_ids=list(rendered.digest_ids), budget=budget,
                    memory_telemetry_version=1, pointer_ids=list(rendered.pointer_ids),
                    full_body_ids=list(rendered.full_body_ids))
                expected = {'agent': 'synthetic-agent', 'session_id': 'synthetic-session',
                            'digest_ids': ['MEM-001', 'MEM-002'], 'digest_count': 2, 'budget': budget,
                            'memory_telemetry_version': 1, 'pointer_ids': pointer_ids,
                            'full_body_ids': full_ids}
                row = db.get_audit_logs('TASK-test')[-1]
                self.assertEqual(row['action'], 'memory_digest_impression')
                self.assertEqual(row['agent'], 'synthetic-agent')
                self.assertEqual(row['payload'], expected)
                self.assertEqual({key: type(value) for key, value in row['payload'].items()},
                    {'agent': str, 'session_id': str, 'digest_ids': list, 'digest_count': int,
                     'budget': int, 'memory_telemetry_version': int,
                     'pointer_ids': list, 'full_body_ids': list})
                raw = db._conn.execute('SELECT payload FROM audit_log ORDER BY id DESC LIMIT 1').fetchone()[0]
                self.assertEqual(raw, json.dumps(expected))
                self.assertTrue(all(type(mid) is str for key in
                    ('digest_ids', 'pointer_ids', 'full_body_ids') for mid in row['payload'][key]))
                self.assertEqual(sorted(rendered.digest_ids, key=rendered.text.index),
                                 list(rendered.digest_ids))
                print(f'audit contract budget={budget}: keys/types/JSON bytes/render order PASS')


if __name__ == '__main__':
    unittest.main()
