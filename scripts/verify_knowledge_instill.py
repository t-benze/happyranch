"""S3a real SQLite/MemoryStore exercise, outside retired test suites.

Run: uv run --frozen python scripts/verify_knowledge_instill.py [CASE]
Uses isolated synthetic orgs only. No daemon, provider, native session, HTTP,
CLI import endpoint or live org runs. Archive curation output is synthetic;
publication, import, allocation, read-back, reconciliation and files are real.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import types
from contextlib import contextmanager
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime.infrastructure.database import Database
from runtime.infrastructure.learnings_store import InvalidLearningEntry, MemoryCompactionResult, MemoryItem, MemoryStore
from runtime.knowledge.archives import KnowledgeArchiveStore
from runtime.knowledge.curation import capture_digest
from runtime.knowledge.imports import ImportError, install_import_schema
from runtime.knowledge.instill import InstillLifecycle
from runtime.knowledge.lifecycle_models import ImportOperation, InstillConfirmation
from runtime.knowledge.sources import Coverage, RetiredSource, SourceRecord
from runtime.models import DreamRecord, JobRecord, ScheduleRecord, TaskRecord, WorkHourRecord
from runtime.orchestrator.org_config import MemoryCompactionConfig
from runtime.orchestrator.teams import TeamsRegistry

BASE = 'b90d61fb247e9ca40413d8b22733a13025bc3ea5'
NOW = datetime(2026, 11, 11, tzinfo=timezone.utc)


def require(value: object, message: str) -> None:
    if not value:
        raise AssertionError(message)


def check_operation(operation: ImportOperation, *, imported: int = 0, skipped: int = 0, failed: int = 0) -> None:
    require((operation.imported, operation.skipped, operation.failed, operation.pending) == (imported, skipped, failed, 0),
            'incorrect exact operation accounting')
    require(operation.selected == imported + skipped + failed, 'terminal selected accounting')
    require(operation.state == ('Partially failed' if failed and imported + skipped else 'Failed' if failed else 'Succeeded'),
            'incorrect terminal operation state')


def capture(source_id: str = 'source') -> tuple[RetiredSource, dict]:
    records, items, dispositions = [], [], []
    for index in range(4):
        reference = 'runtime-' + hashlib.sha256(str(index).encode()).hexdigest()
        restricted = index == 3
        body = f'Synthetic source observation {index}.'
        records.append(SourceRecord(reference_id=reference, category='runtime_memory', relative_path=f'MEM-{index+1:03d}-source.md',
                                    sha256=hashlib.sha256(body.encode()).hexdigest(), body=body,
                                    metadata={'authored_by': source_id, 'authored_at': None if index == 1 else '2020-01-01T00:00:00+00:00',
                                              'lifecycle': 'evicted' if restricted else 'valid'}))
        dispositions.append({'reference_id': reference, 'disposition': 'reference_only' if restricted else 'selected',
                             'reason': 'retained reference' if restricted else 'reusable supported observation',
                             'exclusion_category': None, 'count': 1})
        items.append({'item_id': f'item-{index}', 'category': 'lesson_fact', 'title': f'Synthetic lesson {index}',
                      'content': None if restricted else f'CURATED_SENTINEL_{index}: useful synthetic knowledge.',
                      'references': [reference], 'instill_eligible': not restricted,
                      'attribution': [{'reference_id': reference, 'original_author': source_id,
                                       'authored_at': None if index == 1 else '2020-01-01T00:00:00+00:00', 'observed_at': NOW.isoformat(),
                                       'founder_ruling': None, 'founder_ruling_reference': None}],
                      'flags': ['evicted'] if restricted else ['uncertain'],
                      'warnings': [] if restricted else ['Synthetic uncertainty; verify current facts.'],
                      'prerequisites': None, 'limits': None, 'open_work': None})
    source = RetiredSource(source_id=source_id, name='Synthetic source', team='old_team', provider='claude',
                           cutoff=NOW.isoformat(), records=records, coverage=[
                               Coverage(category='runtime_memory', state='included', reason='complete', count=4, cutoff=NOW.isoformat()),
                               Coverage(category='claude_memory', state='unavailable', reason='no_retained_path', cutoff=NOW.isoformat())])
    return source, {'format': 'knowledge-curation@1', 'capture_digest': capture_digest(source),
                    'explicitly_empty': False, 'dispositions': dispositions, 'items': items}


@contextmanager
def fixture() -> Iterator[SimpleNamespace]:
    with tempfile.TemporaryDirectory(prefix='knowledge-instill-') as temporary:
        root = Path(temporary)
        (root / 'org/agents').mkdir(parents=True)
        (root / 'org/teams.yaml').write_text('teams:\n  synthetic:\n    manager: curator\n    workers: [recipient, other]\n')
        for name, role in [('curator', 'manager'), ('recipient', 'worker'), ('other', 'worker')]:
            (root / 'org/agents' / f'{name}.md').write_text(
                f'---\nname: {name}\nteam: synthetic\nrole: {role}\nexecutor: codex\n---\nSynthetic role.\n')
            (root / 'workspaces' / name / 'memory').mkdir(parents=True)
        db = Database(root / 'happyranch.db')
        org = SimpleNamespace(root=root, slug='synthetic', db=db, teams=TeamsRegistry.load(root),
                              teams_lock=asyncio.Lock(), db_lock=asyncio.Lock())
        owner = InstillLifecycle(org)
        store = KnowledgeArchiveStore(db._conn, db._lock, 'synthetic')
        source, result = capture()
        operation = store.submit('synthetic', source, {'confirmed': True})
        store.start(operation['operation_id'], processor_id='curator', task_id='TASK-synthetic', session_id='synthetic-session')
        archive = store.publish(operation['operation_id'], json.dumps(result), result_id=1)
        memory = MemoryStore(root / 'workspaces/recipient/memory')
        try:
            yield SimpleNamespace(org=org, db=db, owner=owner, archives=store, archive=archive, memory=memory)
        finally:
            org.db.close()


def confirmation(f: SimpleNamespace, key: str = 'one', items: list[str] | None = None, **changes: object) -> InstillConfirmation:
    return InstillConfirmation.model_validate({'submission_id': key, 'archive_id': f.archive['archive_id'],
                                               'archive_version': f.archive['version'],
                                               'item_ids': items if items is not None else ['item-0', 'item-1'],
                                               'confirmed': True, **changes})


def submit(f: SimpleNamespace, key: str = 'one', items: list[str] | None = None) -> ImportOperation:
    return asyncio.run(f.owner.submit('recipient', confirmation(f, key, items)))


def snapshot(f: SimpleNamespace) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in f.memory.root.glob('*.md')}


def refused(f: SimpleNamespace, body: InstillConfirmation, code: str, recipient: str = 'recipient') -> ImportError:
    before = snapshot(f)
    counts = tuple(f.db._conn.execute('SELECT (SELECT COUNT(*) FROM knowledge_import_operations),'
                                      '(SELECT COUNT(*) FROM knowledge_import_items),(SELECT COUNT(*) FROM audit_log)').fetchone())
    try:
        asyncio.run(f.owner.submit(recipient, body))
    except ImportError as error:
        require(error.code == code, f'wrong refusal {error.code} expected {code}')
        refusal = error
    else:
        raise AssertionError('forbidden confirmation admitted: ' + code)
    require(snapshot(f) == before, 'refusal modified memory files')
    require(counts == tuple(f.db._conn.execute('SELECT (SELECT COUNT(*) FROM knowledge_import_operations),'
                                              '(SELECT COUNT(*) FROM knowledge_import_items),(SELECT COUNT(*) FROM audit_log)').fetchone()),
            'refusal created durable effects')
    return refusal


def legacy() -> None:
    old = subprocess.check_output(['git', 'show', BASE + ':runtime/infrastructure/db/schema.py'], text=True)
    module = types.ModuleType('s3a_prechange_schema')
    exec(compile(old, '<immutable-prechange-bootstrap>', 'exec'), module.__dict__)

    class PrechangeDatabase(Database):
        _create_tables = module.SchemaMixin._create_tables

    with tempfile.TemporaryDirectory(prefix='instill-upgrade-') as directory:
        path = Path(directory) / 'legacy.db'
        db = PrechangeDatabase(path)
        require(not db._conn.execute("SELECT 1 FROM sqlite_schema WHERE name='knowledge_import_items'").fetchone(),
                'legacy fixture already has imports')
        db.insert_task(TaskRecord(id='TASK-900001', brief='Synthetic legacy task', assigned_agent='recipient'))
        schema = [tuple(r) for r in db._conn.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name')]
        retained = tuple(db._conn.execute("SELECT * FROM tasks WHERE id='TASK-900001'").fetchone())
        db.close()
        db = Database(path)
        require(db._conn.execute("SELECT 1 FROM sqlite_schema WHERE name='knowledge_import_items'").fetchone(),
                'legacy upgrade missing imports')
        require(schema == [tuple(r) for r in db._conn.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE name NOT LIKE '%knowledge_import%' ORDER BY type,name")],
            'legacy schema changed beyond additive import objects')
        require(retained == tuple(db._conn.execute("SELECT * FROM tasks WHERE id='TASK-900001'").fetchone()), 'legacy row changed')
        upgraded = [tuple(r) for r in db._conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE name LIKE '%knowledge_import%' ORDER BY type,name")]
        install_import_schema(db._conn)
        install_import_schema(db._conn)
        db._conn.commit()
        fresh = Database(Path(directory) / 'fresh.db')
        require(upgraded == [tuple(r) for r in fresh._conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE name LIKE '%knowledge_import%' ORDER BY type,name")],
                'fresh/legacy/repeated installer diverged')
        require(db._conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'legacy integrity failure')
        db.close()
        fresh.close()


def happy_repeat_overlap() -> None:
    with fixture() as f:
        operation = submit(f)
        check_operation(operation, imported=2)
        identifiers = [r.memory_id for r in operation.results]
        require(len(set(identifiers)) == 2 and all(i.startswith('MEM-') for i in identifiers), 'local allocation failed')
        for result in operation.results:
            note = f.memory.read_entry(result.memory_id)
            require(note.inherited_from['archive_item_id'] == result.item_id and 'CURATED_SENTINEL' in note.body,
                    'receipt not retrievable with exact content/provenance')
        before = snapshot(f)
        replay = asyncio.run(f.owner.submit('recipient', confirmation(f)))
        require(replay.operation_id == operation.operation_id and snapshot(f) == before, 'original confirmation not reopened')
        repeat = submit(f, 'repeat')
        check_operation(repeat, skipped=2)
        require([r.memory_id for r in repeat.results] == identifiers and snapshot(f) == before, 'repeat wrote duplicate or lost mapping')
        overlap = submit(f, 'overlap', ['item-1', 'item-2'])
        check_operation(overlap, imported=1, skipped=1)
        require(overlap.results[0].memory_id == identifiers[1] and len(f.memory.list_entries()) == 3, 'overlap not globally deduplicated')
        f.db.close()
        f.org.db = Database(f.org.root / 'happyranch.db')
        f.db = f.org.db
        f.owner = InstillLifecycle(f.org)
        require(asyncio.run(f.owner.show(operation.operation_id)) == operation, 'receipt did not survive reopen')
        require(len(asyncio.run(f.owner.list('recipient'))) == 3, 'learned-source operation history unavailable')
        audits = f.db.get_audit_logs('AGENT-recipient')
        require(not any('CURATED_SENTINEL' in json.dumps(row) or 'Original attribution' in json.dumps(row) for row in audits), 'audit copied body')
        additions = [row for row in audits if row['action'] == 'memory_added']
        require(len(additions) == 3 and all(set(row['payload']) == {'id', 'slug', 'topic', 'tags', 'source_task'} for row in additions),
                'memory_added shape/mapping changed')
        with f.owner.store.transaction():
            try:
                f.db._conn.execute("UPDATE knowledge_import_items SET expected_content='changed'")
            except sqlite3.IntegrityError:
                pass
            else:
                raise AssertionError('durable identity not immutable')


def partial_retry() -> None:
    with fixture() as f:
        (f.org.root / 'org/config.yaml').write_text('memory_compaction:\n  enabled: true\n  auto_trigger_entry_count: 1\n')
        original = MemoryStore._validate_entry_structure
        failed = False

        def validation(memory: MemoryStore, entry: MemoryItem) -> None:
            nonlocal failed
            if entry.inherited_from and entry.inherited_from['archive_item_id'] == 'item-1' and not failed:
                failed = True
                raise InvalidLearningEntry('synthetic_prewrite_failure', 'synthetic')
            return original(memory, entry)

        with patch.object(MemoryStore, '_validate_entry_structure', validation):
            first = submit(f)
        check_operation(first, imported=1, failed=1)
        require(first.results[1].retryable and not first.results[1].held, 'proven-unwritten failure unsafe retry flag')
        retained = snapshot(f)
        writes = []
        original_write = MemoryStore.write_inherited_entry

        def writing(memory: MemoryStore, entry: MemoryItem, expected: str) -> MemoryItem:
            writes.append(entry.id)
            return original_write(memory, entry, expected)

        with patch.object(MemoryStore, 'write_inherited_entry', writing), patch.object(MemoryStore, 'compact', wraps=f.memory.compact) as compact:
            final = asyncio.run(f.owner.retry(first.operation_id))
        check_operation(final, imported=2)
        require(final.operation_id == first.operation_id and writes == [final.results[1].memory_id], 'retry selection or missing-only writes changed')
        require(compact.call_count == 0, 'missing-item retry compacted at threshold')
        require(all(snapshot(f)[name] == body for name, body in retained.items() if name != '_index.md'), 'retry rewrote success')


def unwritten_overlap(selected: list[str]) -> None:
    with fixture() as f:
        validate = MemoryStore._validate_entry_structure

        def validation(memory: MemoryStore, entry: MemoryItem) -> None:
            if entry.inherited_from and entry.inherited_from['archive_item_id'] == 'item-1':
                raise InvalidLearningEntry('synthetic_prewrite_failure', 'synthetic')
            validate(memory, entry)

        with patch.object(MemoryStore, '_validate_entry_structure', validation):
            original = submit(f)
        check_operation(original, imported=1, failed=1)
        reserved = f.owner.store.item(f.owner.store.get(original.operation_id), 'item-1')
        require(reserved['state'] == 'unwritten' and f.memory._find_by_id(reserved['memory_id']) is None,
                'overlap fixture did not prove an unwritten reserved item')
        before = snapshot(f)
        audit = f.owner.audit.log_knowledge_import

        def lost_response(operation: ImportOperation) -> None:
            audit(operation)
            raise RuntimeError('synthetic lost operation response')

        with patch.object(f.owner.audit, 'log_knowledge_import', lost_response):
            try:
                submit(f, 'overlap-unwritten', selected)
            except RuntimeError as error:
                require(str(error) == 'synthetic lost operation response', 'wrong response-loss boundary')
            else:
                raise AssertionError('lost response not injected after durable audit')
        overlap = submit(f, 'overlap-unwritten', selected)
        require(snapshot(f) == before, 'fresh overlap wrote another operation unwritten item')
        check_operation(overlap, skipped=len(selected) - 1, failed=1)
        rejected = overlap.results[-1]
        require(rejected.outcome == 'Failed' and rejected.memory_id == reserved['memory_id']
                and rejected.reason == 'knowledge_import_incomplete_intent_retry_original_operation'
                and not rejected.retryable and not rejected.held, 'overlap lacks closed original-operation refusal')
        require(f.owner.store.item(f.owner.store.get(original.operation_id), 'item-1') == reserved,
                'overlap modified the original intent')
        require(submit(f) == original, 'original replay changed its failed/unwritten projection')
        f.db.close()
        f.org.db = Database(f.org.root / 'happyranch.db')
        f.db = f.org.db
        f.owner = InstillLifecycle(f.org)
        require(submit(f, 'overlap-unwritten', selected) == overlap, 'reopen flipped overlap outcomes')
        audit_count = len(f.db.get_audit_logs('AGENT-recipient'))
        require(submit(f) == original and submit(f, 'overlap-unwritten', selected) == overlap,
                'lost-response replay changed either operation')
        require(len(f.db.get_audit_logs('AGENT-recipient')) == audit_count and snapshot(f) == before,
                'replay repeated writes/audits')
        recovered = asyncio.run(f.owner.retry(original.operation_id))
        check_operation(recovered, imported=2)
        require(recovered.results[-1].memory_id == reserved['memory_id'] and len(f.memory.list_entries()) == 2,
                'original retry duplicated or abandoned reserved identity')
        after = snapshot(f)
        require(all(after[name] == body for name, body in before.items() if name != '_index.md'),
                'original retry rewrote completed note')
        require(submit(f) == recovered and submit(f, 'overlap-unwritten', selected) == overlap,
                'post-recovery replay flipped outcomes')
        closed = asyncio.run(f.owner.retry(overlap.operation_id))
        check_operation(closed, skipped=len(selected) - 1, failed=1)
        require(closed.results == overlap.results and snapshot(f) == after,
                'nonretryable overlap changed results or files')
        history = {operation.operation_id: operation for operation in asyncio.run(f.owner.list('recipient'))}
        require(history == {recovered.operation_id: recovered, closed.operation_id: closed}, 'history counts differ')
        audits = f.db.get_audit_logs('AGENT-recipient')
        for operation in (original, overlap, recovered, closed):
            expected = {key: getattr(operation, key) for key in (
                'operation_id', 'state', 'selected', 'imported', 'skipped', 'failed', 'pending')}
            require(any(row['action'] == 'knowledge_import' and
                        all(row['payload'].get(key) == value for key, value in expected.items()) and
                        row['payload']['items'] == [result.model_dump() for result in operation.results]
                        for row in audits), 'feature audit disagrees with persisted item/count outcomes')
        require(len([row for row in audits if row['action'] == 'memory_added']) == 2,
                'overlap/replay duplicated memory additions')
        require(not any('CURATED_SENTINEL' in json.dumps(row) for row in audits), 'audit copied inherited body')
        print('unwritten_overlap ' + repr(selected) + ': PASS', flush=True)


def unwritten_overlap_single() -> None:
    unwritten_overlap(['item-1'])


def unwritten_overlap_mixed() -> None:
    unwritten_overlap(['item-0', 'item-1'])


def completed_receipt_recovery() -> None:
    with fixture() as f:
        save = f.owner.store.save_results

        class Interrupted(BaseException):
            pass

        def interrupt(operation_id: str, results: list, *, terminal: bool) -> None:
            if results[0].outcome == 'Imported':
                raise Interrupted()
            save(operation_id, results, terminal=terminal)

        with patch.object(f.owner.store, 'save_results', interrupt):
            try:
                submit(f)
            except Interrupted:
                pass
            else:
                raise AssertionError('receipt/accounting interruption not reached')
        operation = f.owner.store.find('recipient', 'one')
        row = f.owner.store.item(operation, 'item-0')
        require(row['state'] == 'completed' and f.memory.read_entry(row['memory_id']) is not None,
                'interruption preceded real completed receipt')
        pending = asyncio.run(f.owner.show(operation['operation_id']))
        require(pending.imported == 1 and pending.pending == 1 and pending.results[0].outcome == 'Imported',
                'completed receipt lost durable Imported outcome before result save')
        before = snapshot(f)
        recovered = asyncio.run(f.owner.retry(operation['operation_id']))
        check_operation(recovered, imported=2)
        require(all(snapshot(f)[name] == body for name, body in before.items() if name != '_index.md'),
                'receipt recovery rewrote committed note')
        after = snapshot(f)
        require(submit(f) == recovered and asyncio.run(f.owner.retry(operation['operation_id'])) == recovered,
                'completed operation replay/retry changed Imported outcomes')
        repeat = submit(f, 'after-recovery')
        check_operation(repeat, skipped=2)
        require(snapshot(f) == after and len(f.memory.list_entries()) == 2, 'completed mapping replay wrote duplicates')
        # Even the reserving operation sees Skipped on an already-completed
        # mapping; recovery retains Imported from the atomic durable result.
        existing = f.owner._one(f.owner.store.get(operation['operation_id']),
                               f.owner._selection('recipient', confirmation(f))['item-0'], f.memory)
        require(existing.outcome == 'Skipped', 'completed-mapping label depends on reservation owner')


def held_reconciliation() -> None:
    for mode in ('match', 'contradictory', 'missing', 'unreadable'):
        with fixture() as f:
            transition = f.owner.store.transition_item

            def fail_receipt(operation: dict, item_id: str, state: str, reason: str | None = None) -> None:
                if state == 'completed':
                    raise sqlite3.OperationalError('synthetic receipt failure')
                return transition(operation, item_id, state, reason)

            with patch.object(f.owner.store, 'transition_item', fail_receipt):
                first = submit(f, items=['item-0'])
            check_operation(first, failed=1)
            require(first.results[0].held and not first.results[0].retryable, 'possible write not held')
            identifier = first.results[0].memory_id
            path = f.memory._find_by_id(identifier)
            require(path is not None, 'failure was not after a real write')
            if mode == 'contradictory':
                path.write_text('Contradictory synthetic bytes.\n')
            if mode == 'missing':
                path.unlink()  # test-side loss, never product rollback
            before = snapshot(f)
            writes = []
            original_write = MemoryStore.write_inherited_entry

            def writing(memory: MemoryStore, entry: MemoryItem, expected: str) -> MemoryItem:
                writes.append(entry.id)
                return original_write(memory, entry, expected)

            with patch.object(MemoryStore, 'write_inherited_entry', writing):
                if mode == 'unreadable':
                    original_read = Path.read_bytes

                    def unreadable(p: Path) -> bytes:
                        if p == path:
                            raise OSError('synthetic unreadable file')
                        return original_read(p)

                    with patch.object(Path, 'read_bytes', unreadable):
                        final = asyncio.run(f.owner.retry(first.operation_id))
                else:
                    final = asyncio.run(f.owner.retry(first.operation_id))
            require(not writes and final.results[0].memory_id == identifier, 'reconciliation allocated or wrote again')
            if mode == 'match':
                check_operation(final, imported=1)
                require(not final.results[0].held, 'matching reserved write not finalized')
            else:
                check_operation(final, failed=1)
                require(final.results[0].held and not final.results[0].retryable and snapshot(f) == before,
                        mode + ' evidence escaped uncertainty hold')
        print('  held_' + mode + ': PASS')


def preservation_provenance() -> None:
    with fixture() as f:
        for index, lifecycle in enumerate(('valid', 'superseded', 'evicted')):
            entry = MemoryItem(id=f'MEM-{index+1:03d}', slug=f'existing-{index}', title='Old synthetic note', topic='old',
                               body='Original bytes must remain.\n', provenance='directive' if index == 1 else 'experiential',
                               lifecycle=lifecycle, salience=0)
            f.memory.write_entry(entry, 'recipient')
        f.memory.regenerate_index()
        before = snapshot(f)
        (f.org.root / 'org/config.yaml').write_text('memory_compaction:\n  enabled: true\n  auto_trigger_entry_count: 3\n  salience_floor: 10\n  stale_days: 1\n')
        calls = []
        compact = MemoryStore.compact

        def compaction(memory: MemoryStore, *args: object, **kwargs: object) -> MemoryCompactionResult:
            calls.append(True)
            return compact(memory, *args, **kwargs)

        with patch.object(MemoryStore, 'compact', compaction):
            first = submit(f)
            repeat = submit(f, 'repeat')
            retry = asyncio.run(f.owner.retry(first.operation_id))
        check_operation(first, imported=2)
        check_operation(repeat, skipped=2)
        require(not calls and retry == first, 'import/retry triggered compaction at threshold')
        require(all(snapshot(f)[name] == content for name, content in before.items() if name != '_index.md'),
                'existing note bytes/ids/lifecycle/provenance changed')
        identifier = first.results[0].memory_id
        note = f.memory.read_entry(identifier)
        block = note.inherited_from
        require(note.provenance == 'inherited' and block['source_id'] == 'source' and
                block['source_dates'][0]['authored_at'].startswith('2020-')
                and f.memory.read_entry(first.results[1].memory_id).inherited_from['source_dates'][0]['authored_at'] is None
                and note.imported_at != block['source_dates'][0]['authored_at'], 'source/import dates or unknowns lost')
        require('Original attribution' in note.body and 'Synthetic uncertainty' in note.body and "not the recipient's experience" in note.body,
                'readable source attribution/warnings lost')
        hit = next(h for h in f.memory.search('CURATED_SENTINEL') if h.id == identifier)
        summary = next(s for s in f.memory.list_entries() if s.id == identifier)
        require(hit.provenance == 'inherited' and hit.inherited_from == block == summary.inherited_from,
                'read/search/list provenance lost')
        index = (f.memory.root / '_index.md').read_text()
        require(f"archive `{f.archive['archive_id']}` v1 item `item-0`" in index and 'Inherited from `source`' in index,
                'index lost inherited source/archive mapping')
        f.memory.update_entry(identifier, MemoryItem(id=identifier, slug='edited', title='Edited synthetic title', topic='edited', body='Edited content.'), 'recipient')
        reread = f.memory.read_entry(identifier)
        require(reread.inherited_from == block and reread.imported_at == note.imported_at and reread.provenance == 'inherited',
                'supported update stripped inherited provenance')
        # A threshold-active ordinary writer must still invoke ordinary compaction.
        ordinary = MemoryStore(f.memory.root, MemoryCompactionConfig(enabled=True, auto_trigger_entry_count=1))
        with patch.object(MemoryStore, 'compact', compaction):
            ordinary.write_entry(MemoryItem(id=ordinary.next_id(), slug='ordinary', title='Ordinary', topic='normal', body='ordinary'), 'recipient')
        require(calls, 'ordinary compaction was globally disabled')


def seed_blocker(f: SimpleNamespace, kind: str, *, agent: str = 'recipient') -> str:
    if kind == 'task':
        f.db.insert_task(TaskRecord(id='TASK-100', brief='Synthetic active task', assigned_agent=agent))
        return 'TASK-100'
    if kind == 'dream':
        f.db.insert_dream(DreamRecord(id='DREAM-100', agent_name=agent, local_date='2026-11-11', scheduled_for=NOW,
                                     window_end=NOW, status='running'))
        return 'DREAM-100'
    if kind == 'work_hour':
        f.db.work_hours.insert(WorkHourRecord(id='WORKHOUR-100', agent_name=agent, local_date='2026-11-11', slot='synthetic',
                                            mode='windowed', scheduled_for=NOW, status='running'))
        return 'WORKHOUR-100'
    if kind == 'schedule':
        f.db.schedules.insert(ScheduleRecord(id='SCHED-100', agent_name=agent, team='synthetic', kind='one_shot', fire_at=NOW,
                                            normalized_brief='Synthetic', source_instruction='Synthetic', status='firing'))
        return 'SCHED-100'
    if kind == 'job':
        f.db.insert_job(JobRecord(id='JOB-100', task_id='TASK-synthetic', agent_name=agent, title='Synthetic', rationale='Synthetic',
                                 script_text='true', interpreter='bash', status='pending', created_at=NOW.isoformat()))
        return 'JOB-100'
    f.db._conn.execute("INSERT INTO threads(id,subject,started_at) VALUES ('THR-synthetic','Synthetic',?)", (NOW.isoformat(),))
    f.db._conn.execute("INSERT INTO thread_invocations(thread_id,agent_name,invocation_token,triggering_seq,purpose,status,enqueued_at,started_at) "
                       "VALUES ('THR-synthetic',?,'synthetic-token',1,'reply','pending',?,?)", (agent, NOW.isoformat(), NOW.isoformat()))
    f.db._conn.commit()
    return 'synthetic-token'


def busy_all_six() -> None:
    for kind in ('task', 'thread_invocation', 'schedule', 'work_hour', 'dream', 'job'):
        with fixture() as f:
            identifier = seed_blocker(f, kind)
            error = refused(f, confirmation(f), 'knowledge_import_busy_finish_wait_then_retry')
            require({'kind': kind, 'id': identifier} in error.blockers, 'Busy omitted actual ' + kind + ' reference')
        print('  busy_' + kind + ': PASS')


def history_caps() -> None:
    with fixture() as f:
        seed_blocker(f, 'task')
        seed_blocker(f, 'dream')
        # Bulk synthetic history only; real exhaustive readers and point parsers
        # still drive admission. No fake list implements the census.
        f.db._conn.executemany("INSERT INTO tasks(id,brief,status,team,assigned_agent,created_at,updated_at) VALUES (?,?,'completed','synthetic','recipient',?,?)",
                               [(f'TASK-{i+1000}', 'Synthetic terminal task', '2027-01-01', '2027-01-01') for i in range(10001)])
        f.db._conn.executemany("INSERT INTO dreams(id,agent_name,local_date,scheduled_for,window_end,status,created_at) "
                               "VALUES (?,'recipient',?,? ,?,'completed',?)",
                               [(f'DREAM-{i+1000}', str(i), '2027-01-01', '2027-01-01', '2027-01-01') for i in range(501)])
        f.db._conn.commit()
        require(not any(t.id == 'TASK-100' for t in f.db.list_tasks(assigned_agent='recipient', limit=10000)), 'task not actually behind cap')
        require(not any(d.id == 'DREAM-100' for d in f.db.list_dreams(agent='recipient', limit=500)), 'dream not actually behind cap')
        error = refused(f, confirmation(f), 'knowledge_import_busy_finish_wait_then_retry')
        require({'kind': 'task', 'id': 'TASK-100'} in error.blockers and {'kind': 'dream', 'id': 'DREAM-100'} in error.blockers,
                'history cap hid active blocker')


def incomplete_census() -> None:
    for kind in ('task', 'thread_invocation', 'schedule', 'work_hour', 'dream', 'job'):
        with fixture() as f:
            target, method = {
                'task': (f.db, 'get_nonterminal_task_ids'), 'thread_invocation': (f.db, 'list_started_invocations_for_agent'),
                'schedule': (f.db.schedules, 'list_ids_by_status'), 'work_hour': (f.db.work_hours, 'list_ids_by_status'),
                'dream': (f.db, 'list_dream_ids_by_status'), 'job': (f.db, 'list_job_ids_by_status'),
            }[kind]
            with patch.object(target, method, side_effect=sqlite3.OperationalError('synthetic read failure')):
                refused(f, confirmation(f), 'knowledge_import_idle_read_failed')
        if kind != 'thread_invocation':
            with fixture() as f:
                seed_blocker(f, kind, agent='')
                refused(f, confirmation(f), 'knowledge_import_attribution_unknown_' + kind)
        print('  incomplete_' + kind + ': PASS')
    with fixture() as f:
        seed_blocker(f, 'task', agent='other')
        with patch.object(f.db, 'get_task', return_value=None):
            refused(f, confirmation(f), 'knowledge_import_idle_incomplete_task')
    with fixture() as f:
        seed_blocker(f, 'task', agent='other')
        # Well-attributed other work is not Busy for this recipient.
        check_operation(submit(f), imported=2)


def confirmation_refusals() -> None:
    with fixture() as f:
        for change in ({'item_ids': []}, {'confirmed': False}, {'item_ids': ['item-0', 'item-0']}):
            try:
                confirmation(f, **change)
            except ValidationError:
                pass
            else:
                raise AssertionError('invalid confirmation accepted')
        refused(f, confirmation(f, archive_id='not-ready'), 'knowledge_import_ready_same_org_archive_required')
        for state in ('Queued', 'Processing', 'Failed'):
            source, result = capture('source_' + state.lower())
            attempt = f.archives.submit(state, source, {'confirmed': True})
            if state == 'Processing':
                f.archives.start(attempt['operation_id'], processor_id='curator')
            elif state == 'Failed':
                f.archives.fail(attempt['operation_id'], 'synthetic_failed')
            refused(f, confirmation(f, archive_id=attempt['operation_id']), 'knowledge_import_ready_same_org_archive_required')
        foreign = KnowledgeArchiveStore(f.db._conn, f.db._lock, 'foreign')
        source, result = capture()
        operation = foreign.submit('foreign', source, {'confirmed': True})
        foreign.start(operation['operation_id'], processor_id='curator')
        archive = foreign.publish(operation['operation_id'], json.dumps(result))
        refused(f, confirmation(f, archive_id=archive['archive_id']), 'knowledge_import_ready_same_org_archive_required')
        refused(f, confirmation(f, archive_version=99), 'knowledge_import_archive_version_mismatch')
        refused(f, confirmation(f, items=['missing']), 'knowledge_import_unknown_item')
        refused(f, confirmation(f, items=['item-3']), 'knowledge_import_item_ineligible')
        refused(f, confirmation(f), 'knowledge_import_source_equals_recipient', recipient='source')
        refused(f, confirmation(f), 'knowledge_import_recipient_ineligible', recipient='founder')
        definition = f.org.root / 'org/agents/recipient.md'
        original = definition.read_bytes()
        definition.unlink()
        refused(f, confirmation(f), 'knowledge_import_recipient_not_active_authorized')
        (f.org.root / 'org/agents/_pending').mkdir()
        (f.org.root / 'org/agents/_pending/recipient.md').write_bytes(original)
        refused(f, confirmation(f), 'knowledge_import_recipient_not_active_authorized')
        (f.org.root / 'org/agents/_pending/recipient.md').unlink()
        (f.org.root / 'org/agents/_terminated').mkdir()
        (f.org.root / 'org/agents/_terminated/recipient.md').write_bytes(original)
        refused(f, confirmation(f), 'knowledge_import_recipient_not_active_authorized')
        (f.org.root / 'org/agents/_terminated/recipient.md').unlink()
        definition.write_bytes(original.replace(b'executor: codex', b'executor: nonexistent'))
        refused(f, confirmation(f), 'knowledge_import_recipient_not_executable')
        definition.write_bytes(original)
        f.memory.root.rmdir()
        refused(f, confirmation(f), 'knowledge_import_memory_uninitialized_or_unwritable')
        f.memory.root.mkdir(mode=0o500)
        refused(f, confirmation(f), 'knowledge_import_memory_uninitialized_or_unwritable')
        f.memory.root.chmod(0o700)
        # One queued confirmation is an operation even before the synchronous
        # processing owner resumes it; a second operation must not allocate.
        body = confirmation(f)
        first = f.owner.store.create('recipient', 'source', body)
        refused(f, confirmation(f, 'second'), 'knowledge_import_inflight_reopen_operation')
        refused(f, confirmation(f, items=['item-2']), 'knowledge_import_confirmation_conflict')
        final = asyncio.run(f.owner.retry(first['operation_id']))
        check_operation(final, imported=2)


def lost_eligibility() -> None:
    with fixture() as f:
        write = MemoryStore.write_inherited_entry

        def writing(memory: MemoryStore, entry: MemoryItem, expected: str) -> MemoryItem:
            result = write(memory, entry, expected)
            # Synthetic loss at an existing file-I/O boundary, no production
            # test parameter/export. The next item must stop before writing.
            (f.org.root / 'org/agents/recipient.md').unlink()
            return result

        with patch.object(MemoryStore, 'write_inherited_entry', writing):
            result = submit(f)
        check_operation(result, imported=1, failed=1)
        require(result.results[1].reason == 'knowledge_import_recipient_not_active_authorized' and len(f.memory.list_entries()) == 1,
                'eligibility loss did not stop remaining items')


def concurrent_admission() -> None:
    with fixture() as f:
        entered, attempted, finished = threading.Event(), threading.Event(), threading.Event()
        original_write = MemoryStore.write_inherited_entry
        errors = []
        exclusion = []

        def task_start() -> None:
            try:
                require(entered.wait(5), 'import never reached actual write')
                acquired = f.db._lock.acquire(blocking=False)
                exclusion.append(not acquired)
                if acquired:
                    f.db._lock.release()
                attempted.set()
                seed_blocker(f, 'task')  # real DB writer acquires shared RLock
                finished.set()
            except BaseException as error:
                errors.append(error)

        def writing(memory: MemoryStore, entry: MemoryItem, expected: str) -> MemoryItem:
            entered.set()
            require(attempted.wait(5), 'concurrent task writer did not attempt')
            require(not finished.is_set(), 'task started across census/write RLock')
            return original_write(memory, entry, expected)

        thread = threading.Thread(target=task_start)
        thread.start()
        try:
            with patch.object(MemoryStore, 'write_inherited_entry', writing):
                first = submit(f)
        finally:
            thread.join(5)
        require(not thread.is_alive() and not errors and finished.is_set(), 'concurrent writer failed to settle')
        require(exclusion == [True], 'shared RLock did not exclude concurrent task start')
        check_operation(first, imported=2)
        refused(f, confirmation(f, 'after-start'), 'knowledge_import_busy_finish_wait_then_retry')

    with fixture() as f:
        async def queued_start() -> None:
            # Same lock as ordinary memory mutations/root submission. The
            # importer waits, then sees the start before checking or writing.
            async with f.org.db_lock:
                pending = asyncio.create_task(f.owner.submit('recipient', confirmation(f)))
                await asyncio.sleep(0)
                require(not pending.done(), 'import ignored coordinated admission lock')
                seed_blocker(f, 'task')
            try:
                await pending
            except ImportError as error:
                require(error.blockers == [{'kind': 'task', 'id': 'TASK-100'}], 'start winner did not make import Busy')
            else:
                raise AssertionError('concurrent start admitted import')

        asyncio.run(queued_start())
        require(not snapshot(f), 'start winner still wrote recipient memory')


CASES = {f.__name__: f for f in (
    legacy, happy_repeat_overlap, partial_retry, unwritten_overlap_single, unwritten_overlap_mixed, completed_receipt_recovery,
    held_reconciliation, preservation_provenance,
    busy_all_six, history_caps, incomplete_census, confirmation_refusals, lost_eligibility, concurrent_admission)}


if __name__ == '__main__':
    selected = [sys.argv[1]] if len(sys.argv) > 1 else list(CASES)
    for name in selected:
        CASES[name]()
        print(name + ': PASS', flush=True)
    print(json.dumps({'cases': selected, 'passed': len(selected), 'boundary': 'synthetic real SQL/files; no HTTP/provider/session'}))
