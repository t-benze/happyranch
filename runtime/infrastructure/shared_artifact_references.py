"""Same-writer-fence reference proof for shared artifact generations.

No persistent reference cache: each proof reads the complete live projection. A pass
may reuse a projection only while holding its original BEGIN IMMEDIATE fence.
Missing/capped reader evidence is uncertainty, never an empty dependency set.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, unquote, urlsplit

from runtime.infrastructure.artifact_store import ArtifactStore, InvalidArtifactName
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.org_config import load_org_config

_TASK = re.compile(r"(?<![A-Za-z0-9_-])TASK-[0-9]+(?![A-Za-z0-9_-])")
_ORG_TASK = re.compile(r"/(?:api/v1/)?orgs/([^/]+)/tasks/(?P<task>TASK-[0-9]+)(?=/|$)")
_EXPLICIT = re.compile(r"\[artifact:([^\]]*)\]")
_TOKEN = re.compile(r"[^\s`\"'<>\[\]{}(),;!*]+")
_LIVE = {'pending', 'in_progress', 'escalated'}
_STATUSES = _LIVE | {'completed', 'failed', 'cancelled', 'superseded'}
_MAX_ROWS = 100_000
_MAX_BYTES = 32 * 1024 * 1024


class ReferenceUnavailable(ValueError):
    """The complete proof could not be established within its bound."""


class ReferenceProof:
    def __init__(self, conn: sqlite3.Connection, *, slug: str,
                 store: ArtifactStore, deadline: float, origin: str | None = None) -> None:
        if not conn.in_transaction:
            raise ReferenceUnavailable('writer_fence_required')
        self.conn, self.slug, self.store = conn, slug, store
        self.deadline, self.origin = deadline, origin
        self.references: dict[tuple[str, int | None], set[str]] = defaultdict(set)
        self.uncertain_sources: set[str] = set()
        self.rows = self.bytes = 0
        self.revision = (conn.total_changes, conn.execute('PRAGMA data_version').fetchone()[0])
        self._scan()
        self._fresh()

    def _check(self) -> None:
        if time.monotonic() >= self.deadline or self.rows > _MAX_ROWS or self.bytes > _MAX_BYTES:
            raise ReferenceUnavailable('reference_scan_incomplete')

    def _read(self, sql: str, values: tuple = (), *, count_bytes: bool = True) -> list[dict]:
        result = []
        for row in self.conn.execute(sql, values):
            self.rows += 1
            if count_bytes:
                self.bytes += sum(len(v.encode('utf-8')) for v in row if isinstance(v, str))
            self._check()
            result.append(dict(row))
        return result

    def _fresh(self) -> None:
        self._check()
        if not self.conn.in_transaction or self.revision != (
            self.conn.total_changes, self.conn.execute('PRAGMA data_version').fetchone()[0]
        ):
            raise ReferenceUnavailable('reference_snapshot_changed')

    def _add(self, name: str, source: str, generation: int | None = None) -> None:
        if not isinstance(name, str):
            raise ReferenceUnavailable('malformed_reference')
        try:
            self.store.validate_name(name)
        except InvalidArtifactName as exc:
            raise ReferenceUnavailable('malformed_reference') from exc
        if generation is not None and (type(generation) is not int or generation < 1):
            raise ReferenceUnavailable('malformed_generation')
        self.references[name, generation].add(source)

    @staticmethod
    def _strings(value: object) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            return [s for v in value.values() for s in ReferenceProof._strings(v)]
        if isinstance(value, list):
            return [s for v in value for s in ReferenceProof._strings(v)]
        return []

    def _text(self, text: str | None, source: str) -> set[str]:
        if text is None:
            return set()
        if not isinstance(text, str):
            raise ReferenceUnavailable('malformed_text')
        self._check()
        for match in _EXPLICIT.finditer(text):
            self._add(match[1], source)
        if text.count('[artifact:') != len(_EXPLICIT.findall(text)):
            raise ReferenceUnavailable('malformed_reference')
        foreign_handoffs = set()
        for match in _TOKEN.finditer(text):
            token = match[0]
            parsed = urlsplit(token)
            qualified = _ORG_TASK.match(parsed.path)
            if qualified and qualified[1] != self.slug:
                # Exclude this path segment only; a bare occurrence of the same
                # task ID elsewhere in the text remains a required handoff.
                foreign_handoffs.add(match.start() + token.index(parsed.path) + qualified.start('task'))
            prefix = f'/api/v1/orgs/{self.slug}/artifacts/'
            if parsed.path.startswith(prefix):
                if parsed.netloc and (not self.origin or
                                      (parsed.scheme, parsed.netloc) != (urlsplit(self.origin).scheme, urlsplit(self.origin).netloc)):
                    continue
                encoded = parsed.path[len(prefix):]
                if re.search(r'%(?![0-9A-Fa-f]{2})', encoded):
                    raise ReferenceUnavailable('malformed_url')
                name = unquote(encoded, errors='strict')
                query = parse_qs(parsed.query, keep_blank_values=True)
                generation = query.get('generation')
                if generation and (len(generation) != 1 or not generation[0].isdigit()):
                    raise ReferenceUnavailable('malformed_generation')
                self._add(name, source, int(generation[0]) if generation else None)
            elif (not parsed.netloc and not parsed.query and not parsed.fragment
                  and (not parsed.scheme or token.endswith(':'))):
                # Keep the whole binary-spelled key and a token delimited by
                # terminal prose punctuation. No internal substring matching.
                for candidate in dict.fromkeys((token, token.rstrip('.:?'))):
                    try:
                        self.store.validate_name(candidate)
                    except InvalidArtifactName:
                        continue
                    self.references[candidate, None].add(source)
        return {m[0] for m in _TASK.finditer(text) if m.start() not in foreign_handoffs}

    def _attachments(self, value: object, source: str) -> None:
        """Declared shared attachment payloads preserve generation pins."""
        if isinstance(value, str):
            self._text(value, source)
        elif isinstance(value, list):
            for item in value:
                self._attachments(item, source)
        elif isinstance(value, dict):
            name = value.get('artifact_name')
            if name:
                self._add(name, source, value.get('generation'))
            elif not value.get('thread_attachment_id'):
                for item in value.values():
                    self._attachments(item, source)
        else:
            raise ReferenceUnavailable('malformed_attachment_payload')

    def _scan(self) -> None:
        tasks = {r['id']: r for r in self._read(
            'SELECT id,status,parent_task_id FROM tasks', count_bytes=False)}
        if any(t['status'] not in _STATUSES for t in tasks.values()):
            raise ReferenceUnavailable('unknown_task_status')
        children: dict[str, list[str]] = defaultdict(list)
        for t in tasks.values():
            if not isinstance(t['id'], str) or not t['id'] or (
                t['parent_task_id'] is not None and not isinstance(t['parent_task_id'], str)
            ):
                raise ReferenceUnavailable('malformed_task_identity')
            children[t['parent_task_id']].append(t['id'])
        live = [t['id'] for t in tasks.values() if t['status'] in _LIVE]
        todo = set(live) | {child for t in live for child in children[t]}
        visited = set()
        while todo:
            # Historical text is irrelevant unless a live task/direct child or
            # an explicit handoff needs it. Discover that closure incrementally
            # within this same writer fence; cycles never reload text/results.
            batch = tuple(sorted(todo)[:250])
            if any(task_id not in tasks for task_id in batch):
                raise ReferenceUnavailable('required_handoff_missing')
            visited.update(batch)
            todo.difference_update(batch)
            placeholders = ','.join('?' for _ in batch)
            selected = self._read(
                f'SELECT id,brief,final_output_summary FROM tasks WHERE id IN ({placeholders})', batch)
            if len(selected) != len(batch):
                raise ReferenceUnavailable('required_handoff_missing')
            for task in selected:
                todo.update(self._text(task['brief'], task['id']) - visited)
                todo.update(self._text(task['final_output_summary'], task['id']) - visited)
            for r in self._read(
                'SELECT task_id,output_summary,decision_json,risks_flagged FROM task_results '
                f'WHERE task_id IN ({placeholders})', batch
            ):
                task_id = r['task_id']
                todo.update(self._text(r['output_summary'], task_id) - visited)
                for key in ('decision_json', 'risks_flagged'):
                    if r[key]:
                        decoded = json.loads(r[key])
                        if not isinstance(decoded, dict if key == 'decision_json' else list):
                            raise ReferenceUnavailable('malformed_result_payload')
                        for value in self._strings(decoded):
                            todo.update(self._text(value, task_id) - visited)
        threads = self._read('SELECT id,status FROM threads')
        if any(t['status'] not in ('open', 'archived') for t in threads):
            raise ReferenceUnavailable('unknown_thread_status')
        for r in self._read("SELECT m.* FROM thread_messages m JOIN threads t ON t.id=m.thread_id WHERE t.status='open'"):
            self._text(r['body_markdown'], r['thread_id'])
            if r['system_payload_json']:
                payload = json.loads(r['system_payload_json'])
                if not isinstance(payload, dict):
                    raise ReferenceUnavailable('malformed_system_payload')
                for key in ('attachments', 'attachment_names', 'shared_artifacts'):
                    if key in payload:
                        self._attachments(payload[key], r['thread_id'])
        links = self._read("SELECT l.name,l.generation,l.thread_id,l.message_seq,l.ordinal FROM shared_artifact_thread_links l JOIN threads t ON t.id=l.thread_id WHERE t.status='open'")
        pinned = {(r['thread_id'], r['message_seq'], r['ordinal']) for r in links}
        for r in links:
            self._add(r['name'], r['thread_id'], r['generation'])
        for r in self._read("SELECT a.* FROM thread_message_attachments a JOIN threads t ON t.id=a.thread_id WHERE t.status='open'"):
            if r['artifact_name'] and (r['thread_id'], r['message_seq'], r['ordinal']) not in pinned:
                self._add(r['artifact_name'], r['thread_id'])
        versions = {r['id']: r for r in self._read(
            'SELECT v.id,v.skill_id,v.content_artifact_key,v.content_hash,s.org_slug,s.retired_at,s.purged_at '
            'FROM custom_skill_versions v LEFT JOIN custom_skills s ON s.id=v.skill_id')}
        for v in versions.values():
            if v['org_slug'] != self.slug:
                raise ReferenceUnavailable('skill_source_provenance_unavailable')
            name = v['content_artifact_key']
            if not isinstance(name, str):
                raise ReferenceUnavailable('malformed_skill_source')
            self.store.validate_name(name)
            if not v['retired_at'] and not v['purged_at']:
                self._add(name, f"B2:{v['id']}")
            else:
                # No authoritative retirement-of-in-flight-reader receipt exists.
                # Retirement is insufficient to authorize source destruction.
                self.uncertain_sources.add(name)
        # Recovery selects current eligible versions, then reads outside the
        # SQL selection fence. All eligible versions above stay live; retirement
        # cannot prove an already-selected reader has stopped. Materialization
        # history supplies additional positive evidence, never reader exclusion.
        for r in self._read('SELECT version_id,skill_id,content_hash,id FROM custom_skill_materializations'):
            v = versions.get(r['version_id'])
            if not v or (v['skill_id'], v['content_hash']) != (r['skill_id'], r['content_hash']):
                raise ReferenceUnavailable('materialization_source_unavailable')
            self._add(v['content_artifact_key'], f"materialization:{r['id']}")

    def outcome(self, row: dict) -> tuple[str, list[str]]:
        self._fresh()
        refs = self.references.get((row['name'], None), set()) | self.references.get((row['name'], row['generation']), set())
        if refs:
            return 'live', sorted(refs)
        if row['name'] in self.uncertain_sources or row.get('protected_source'):
            return 'indeterminate', ['Q3:reader_exclusion_unavailable']
        return 'absent', []


def reference_outcomes(org: object, conn: sqlite3.Connection, rows: list[dict], *,
                       deadline: float, origin: str | None = None) -> dict[tuple[str, int], tuple[str, list[str]]]:
    """Fresh complete projection for a batch inside its final writer fence.

    Caller retains shared_artifact_transaction through transition/unlink/COMMIT.
    Read every outcome before auditing; own audit writes invalidate bookends.
    No projection survives a COMMIT, await, or release of the Database RLock.
    """
    if conn is not org.db._conn or not conn.in_transaction:
        raise ReferenceUnavailable('writer_fence_required')
    try:
        proof = ReferenceProof(conn, slug=org.slug,
            store=org.db._shared_artifact_service.store, deadline=deadline, origin=origin)
        return {(r['name'],r['generation']):proof.outcome(r) for r in rows}
    except (sqlite3.Error, OSError, ValueError, TypeError, RecursionError):
        return {(r['name'],r['generation']):('indeterminate',['reference_scan_unavailable']) for r in rows}


def audit_protection(org: object, row: dict, outcome: str, references: list[str]) -> None:
    """Every sweep rechecks and audits holds, without granting new retention."""
    if outcome != 'absent':
        org.db._shared_artifact_service._event(row, 'artifact_protected', 'daemon', source='system',
            reason='dependency_check_unavailable' if outcome == 'indeterminate' else 'live_dependency',
            references=references, outcome=outcome)


def effective_mode(org: object, conn: sqlite3.Connection, *, now: datetime | None = None) -> tuple[str, str | None]:
    try:
        mode = load_org_config(OrgPaths(org.root)).shared_artifacts_cleanup_mode
    except (OSError, ValueError):
        return 'preview', 'configuration_unavailable'
    if mode != 'enforce':
        return mode, None
    state = conn.execute('SELECT * FROM shared_artifact_org_state WHERE id=1').fetchone()
    now = now or datetime.now(timezone.utc)
    try:
        started = datetime.fromisoformat(state['rollout_started_at'])
        elapsed = started.utcoffset() == timedelta(0) and now >= started + timedelta(days=7)
    except (TypeError, ValueError):
        elapsed = False
    for ok, reason in ((elapsed, 'preview_seven_days_required'),
                       (state['preview_acceptance'], 'preview_acceptance_required'),
                       (state['inventory_review_id'], 'inventory_review_required'),
                       (state['source_protection_verified'], 'source_protection_unverified')):
        if not ok:
            return 'preview', reason
    return 'enforce', None


def install_reference_seams(org: object, *, origin: str | None = None) -> None:
    """Bind A's fail-closed adapters; never sets any operator rollout gate."""
    db = org.db

    def checker(conn: sqlite3.Connection, row: dict) -> str:
        outcome, refs = reference_outcomes(org, conn, [row],
            deadline=time.monotonic()+30, origin=origin)[row['name'],row['generation']]
        audit_protection(org, row, outcome, refs)
        return outcome

    def mode(conn: sqlite3.Connection) -> str:
        chosen, reason = effective_mode(org, conn)
        if reason:
            db.insert_audit_log_uncommitted(task_id='config:shared_artifacts', agent='daemon',
                action='artifact_mode_refused', payload={'effective_mode': chosen, 'reason': reason})
        return chosen

    db._shared_artifact_reference_checker = checker
    db._shared_artifact_effective_mode = mode
