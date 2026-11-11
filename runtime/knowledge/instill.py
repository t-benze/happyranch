"""S3a synchronous Instill owner; HTTP/CLI/caller authentication belong to S3b.

No new task, provider, executor or permission is created. Existing roster/team
admission revalidates the recipient. Both async locks plus the DB RLock span a
no-await census/write segment. Supported event-loop starts/writers cannot run
inside it; DB worker threads cannot change task/start rows across the RLock.
External same-UID/multiprocess filesystem writers are not fenced by these locks.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import TYPE_CHECKING

from runtime.infrastructure.audit_logger import AuditLogger
from runtime.infrastructure.learnings_store import InvalidLearningEntry, MemoryItem, MemoryStore
from runtime.knowledge.admission import collect_import_blockers
from runtime.knowledge.archives import ArchiveError, KnowledgeArchiveStore, _json, _now
from runtime.knowledge.curation import CuratedItem
from runtime.knowledge.imports import ImportError, KnowledgeImportStore
from runtime.knowledge.lifecycle_models import ImportItemResult, ImportOperation, InstillConfirmation
from runtime.knowledge.sources import SOURCE_ID
from runtime.orchestrator import prompt_loader
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.org_config import load_org_config
from runtime.orchestrator.executor_registry import get_registry

if TYPE_CHECKING:
    from runtime.daemon.org_state import OrgState


class InstillLifecycle:
    def __init__(self, org: OrgState) -> None:
        self.org = org
        self.store = KnowledgeImportStore(org.db._conn, org.db._lock, org.slug)
        self.archives = KnowledgeArchiveStore(org.db._conn, org.db._lock, org.slug)
        self.audit = AuditLogger(org.db)

    def _recipient(self, recipient_id: str) -> MemoryStore:
        from fastapi import HTTPException
        from runtime.daemon.routes.tasks import SubmitTask, resolve_task_owner

        if SOURCE_ID.fullmatch(recipient_id) is None or recipient_id == 'founder':
            raise ImportError('knowledge_import_recipient_ineligible')
        try:
            # Reuse current native executable owner/team/role checks, without
            # allocating a task or changing any authorization behavior.
            resolve_task_owner(SubmitTask(owner=recipient_id, brief=''), self.org)
            definitions = {a.name: a for a in prompt_loader.list_agents(OrgPaths(root=self.org.root))}
            if not get_registry().is_registered(definitions[recipient_id].executor.lower()):
                raise ImportError('knowledge_import_recipient_not_executable')
        except HTTPException:
            raise ImportError('knowledge_import_recipient_not_active_authorized') from None
        except ImportError:
            raise
        except Exception:
            raise ImportError('knowledge_import_recipient_unavailable') from None
        workspace = self.org.root / 'workspaces' / recipient_id
        memory = workspace / 'memory'
        if (workspace.is_symlink() or memory.is_symlink() or not memory.is_dir()
                or not os.access(memory, os.W_OK | os.X_OK)
                or not memory.stat().st_mode & 0o222):
            raise ImportError('knowledge_import_memory_uninitialized_or_unwritable')
        config = load_org_config(OrgPaths(root=self.org.root)).memory_compaction
        return MemoryStore(memory, compaction_config=config)

    def _admit(self, recipient_id: str) -> MemoryStore:
        try:
            memory = self._recipient(recipient_id)
            blockers = collect_import_blockers(self.org.db, recipient_id)
        except ImportError:
            raise
        except Exception:
            raise ImportError('knowledge_import_recipient_or_idle_unavailable') from None
        if blockers:
            raise ImportError('knowledge_import_busy_finish_wait_then_retry', blockers)
        return memory

    def _selection(self, recipient_id: str, body: InstillConfirmation) -> dict[str, CuratedItem]:
        try:
            archive = self.archives.get_archive(body.archive_id)
        except ArchiveError:
            raise ImportError('knowledge_import_ready_same_org_archive_required') from None
        if archive['version'] != body.archive_version:
            raise ImportError('knowledge_import_archive_version_mismatch')
        if archive['source_id'] == recipient_id:
            raise ImportError('knowledge_import_source_equals_recipient')
        items = {i['item_id']: CuratedItem.model_validate(i) for i in archive['items']}
        for identifier in body.item_ids:
            if identifier not in items:
                raise ImportError('knowledge_import_unknown_item')
            item = items[identifier]
            if (not item.instill_eligible or not item.content or not item.content.strip()
                    or set(item.flags) & {'superseded', 'evicted', 'stale', 'promoted'}):
                raise ImportError('knowledge_import_item_ineligible')
        return {identifier: items[identifier] for identifier in body.item_ids}

    async def submit(self, recipient_id: str, body: InstillConfirmation) -> ImportOperation:
        async with self.org.teams_lock:
            async with self.org.db_lock:
                with self.org.db._lock:
                    original = self.store.find(recipient_id, body.submission_id)
                    if original is not None:
                        if json.loads(original['confirmation_json']) != body.model_dump():
                            raise ImportError('knowledge_import_confirmation_conflict')
                        return self.store.projection(original['operation_id'])
                    self._selection(recipient_id, body)
                    self._admit(recipient_id)
                    source_id = self.archives.get_archive(body.archive_id)['source_id']
                    operation = self.store.create(recipient_id, source_id, body)
                    return self._run(operation)

    async def retry(self, operation_id: str) -> ImportOperation:
        async with self.org.teams_lock:
            async with self.org.db_lock:
                with self.org.db._lock:
                    operation = self.store.get(operation_id)
                    if operation['state'] == 'Succeeded':
                        return self.store.projection(operation_id)
                    self._admit(operation['recipient_id'])
                    # Do not overlap another confirmed operation on this recipient.
                    if any(o['operation_id'] != operation_id and o['state'] in {'Queued', 'Processing'}
                           for o in self.store.list(operation['recipient_id'])):
                        raise ImportError('knowledge_import_inflight_reopen_operation')
                    return self._run(operation)

    async def show(self, operation_id: str) -> ImportOperation:
        async with self.org.db_lock:
            return self.store.projection(operation_id)

    async def list(self, recipient_id: str) -> list[ImportOperation]:
        async with self.org.db_lock:
            return [self.store.projection(o['operation_id']) for o in self.store.list(recipient_id)]

    def _entry(self, operation: dict, item: CuratedItem, identifier: str) -> MemoryItem:
        now = _now()
        attribution = _json([a.model_dump() for a in item.attribution])
        qualifications = _json({'flags': item.flags, 'warnings': item.warnings, 'prerequisites': item.prerequisites,
                                'limits': item.limits, 'open_work': item.open_work.model_dump() if item.open_work else None})
        body = (f"Inherited from source `{operation['source_id']}`, archive `{operation['archive_id']}` "
                f"v{operation['archive_version']}, item `{item.item_id}`. Imported {now}.\n\n"
                "Historical source material, not the recipient's experience or current authority. "
                "Current charter, permissions, Founder rulings, owners and holds prevail; verify current state.\n\n"
                f"{item.content}\n\nOriginal attribution (null means unknown): {attribution}\n\n"
                f"Warnings and qualifications (null means unknown): {qualifications}\n")
        digest = hashlib.sha256(_json([self.org.slug, operation['archive_id'], operation['archive_version'],
                                      item.item_id, operation['recipient_id']]).encode()).hexdigest()[:40]
        return MemoryItem(id=identifier, slug='inherited-' + digest, title=item.title, topic='inherited', body=body,
                          provenance='inherited', authored_by=operation['recipient_id'], authored_at=now,
                          updated_by=operation['recipient_id'], updated_at=now, imported_at=now,
                          inherited_from={'archive_id': operation['archive_id'], 'archive_version': operation['archive_version'],
                                          'archive_item_id': item.item_id, 'source_id': operation['source_id'],
                                          'source_dates': [{'reference_id': a.reference_id, 'authored_at': a.authored_at,
                                                            'observed_at': a.observed_at} for a in item.attribution]})

    @staticmethod
    def _matches(memory: MemoryStore, row: dict) -> bool:
        """Only exact reserved-ID bytes AND round-tripped metadata prove a write."""
        try:
            paths = [p for p in memory._entry_paths() if p.name.startswith(row['memory_id'] + '-')
                     or p.name.startswith(row['memory_id'].replace('MEM-', 'LRN-') + '-')]
            if len(paths) != 1 or paths[0].is_symlink() or paths[0].read_bytes() != row['expected_content'].encode('utf-8'):
                return False
            entry = memory.read_entry(row['memory_id'])
            return (entry.id == row['memory_id'] and entry.provenance == 'inherited'
                    and memory._serialize(entry) == row['expected_content']
                    and entry.inherited_from is not None
                    and (entry.inherited_from['archive_id'], entry.inherited_from['archive_version'],
                         entry.inherited_from['archive_item_id']) == (row['archive_id'], row['archive_version'], row['item_id']))
        except Exception:
            return False

    def _one(self, operation: dict, item: CuratedItem, memory: MemoryStore) -> ImportItemResult:
        row = self.store.item(operation, item.item_id)
        if row is None:
            identifier = self.store.next_reserved_id(operation['recipient_id'], memory.next_id())
            entry = self._entry(operation, item, identifier)
            row = self.store.reserve(operation, item.item_id, identifier, memory._serialize(entry))
        if row['state'] == 'completed':
            return ImportItemResult(item_id=item.item_id, outcome='Skipped',
                                    memory_id=row['memory_id'], reason='durable_existing_mapping')
        if row['operation_id'] != operation['operation_id']:
            return ImportItemResult(item_id=item.item_id, outcome='Failed', memory_id=row['memory_id'],
                                    reason='knowledge_import_incomplete_intent_retry_original_operation',
                                    held=row['state'] in {'held', 'writing'})
        entry = memory._parse(row['expected_content'])
        if row['state'] in {'held', 'writing'}:
            if not self._matches(memory, row):
                self.store.transition_item(operation, item.item_id, 'held', 'uncertainty_hold_reconcile_reserved_id')
                return ImportItemResult(item_id=item.item_id, outcome='Failed', memory_id=row['memory_id'],
                                        reason='uncertainty_hold_reconcile_reserved_id', held=True)
        else:
            # This validation and absence check happen BEFORE the durable
            # possible-write marker. Only intent/unwritten rows may resume.
            memory._validate_entry_structure(entry)
            if memory._find_by_id(row['memory_id']) is not None or memory._find_by_slug(entry.slug) is not None:
                self.store.transition_item(operation, item.item_id, 'held', 'reserved_identity_conflict')
                return ImportItemResult(item_id=item.item_id, outcome='Failed', memory_id=row['memory_id'],
                                        reason='reserved_identity_conflict', held=True)
            self.store.transition_item(operation, item.item_id, 'writing')
            memory.write_inherited_entry(entry, row['expected_content'])
            if not self._matches(memory, row):
                raise ImportError('knowledge_import_readback_mismatch')
        memory.regenerate_index()
        reason = 'reconciled_mapping' if row['state'] in {'held', 'writing'} else None
        self.store.transition_item(operation, item.item_id, 'completed', reason)
        self.audit.log_memory_added(agent=operation['recipient_id'], id=entry.id, slug=entry.slug,
                                    topic=entry.topic, tags=entry.tags, source_task=None)
        return ImportItemResult(item_id=item.item_id, outcome='Imported', memory_id=row['memory_id'], reason=reason)

    def _run(self, operation: dict) -> ImportOperation:
        confirmation = InstillConfirmation.model_validate_json(operation['confirmation_json'])
        items = self._selection(operation['recipient_id'], confirmation)
        results = [ImportItemResult.model_validate(r) for r in json.loads(operation['results_json'])]
        self.store.save_results(operation['operation_id'], results, terminal=False)
        stopped: str | None = None
        for index, item_id in enumerate(confirmation.item_ids):
            # Successes and closed overlap refusals survive replay/retries.
            if (results[index].outcome in {'Imported', 'Skipped'} or
                    results[index].reason == 'knowledge_import_incomplete_intent_retry_original_operation'):
                continue
            if stopped is None:
                try:
                    memory = self._admit(operation['recipient_id'])
                except ImportError as error:
                    stopped = error.code
            if stopped is not None:
                results[index] = ImportItemResult(item_id=item_id, outcome='Failed', reason=stopped, retryable=True)
                continue
            try:
                results[index] = self._one(operation, items[item_id], memory)
            except Exception as error:
                row = self.store.item(operation, item_id)
                held = row is not None and row['state'] in {'writing', 'held'}
                # A failed receipt COMMIT retains writing even if this second
                # best-effort hold cannot commit. Projection treats it as held.
                if row is not None and row['state'] != 'completed':
                    try:
                        self.store.transition_item(operation, item_id, 'held' if held else 'unwritten',
                                                   'uncertainty_hold_reconcile_reserved_id' if held else 'failed_before_write')
                    except Exception:
                        held = True
                if row is not None and row['state'] == 'completed':
                    # Audit failure cannot erase a committed receipt.
                    results[index] = ImportItemResult(item_id=item_id, outcome='Imported', memory_id=row['memory_id'])
                else:
                    reason = ('uncertainty_hold_reconcile_reserved_id' if held else
                              error.code if isinstance(error, InvalidLearningEntry) else 'failed_before_write')
                    results[index] = ImportItemResult(item_id=item_id, outcome='Failed',
                                                      memory_id=row['memory_id'] if row else None,
                                                      reason=reason, held=held, retryable=not held)
            self.store.save_results(operation['operation_id'], results, terminal=False)
        self.store.save_results(operation['operation_id'], results, terminal=True)
        result = self.store.projection(operation['operation_id'])
        self.audit.log_knowledge_import(result)
        return result
