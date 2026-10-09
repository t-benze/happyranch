"""Internal org naming with caller-owned coroutine serialization.

Filesystem capture occurs outside database/profile/publication leases. Helpers
never acquire another async gate and names never publish canonical authority.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from typing import Any

from runtime.infrastructure.thread_mentions import MessageAddresses

from runtime.orchestrator.agent_def import parse_agent_text
from runtime.orchestrator.teams import TeamsRegistry
from .schema import (MAX_ROWS, NamingError, NAMING_DDL, SCAN_SECONDS,
                     execute_literal, read_rows, transaction, validate_names)

_LABEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', re.ASCII)
_ID = re.compile(r'[a-z0-9_]{1,64}', re.ASCII)
MAX_DEFINITIONS = 4096
MAX_FILE_BYTES = 1024 * 1024
MAX_FILE_TOTAL = 32 * 1024 * 1024


@dataclass(frozen=True)
class Namespace:
    # Sorted (canonical ID, lifecycle), plus byte/inode facts for both captures.
    subjects: tuple[tuple[str, str], ...]
    facts: tuple


@dataclass(frozen=True)
class Subject:
    kind: str
    canonical_id: str
    lifecycle: str
    current_label: str | None
    revision: int | None
    classification: str  # id / current / former (never former forwarding)


def editable_label(label):
    if not isinstance(label, str) or _LABEL.fullmatch(label) is None:
        raise NamingError('invalid_identity_label')
    return label


def _identity(st):
    return (st.st_dev, st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _bounded_file(path, deadline):
    if time.monotonic() > deadline:
        raise NamingError('naming_scan_deadline')
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    fd = os.open(path, flags)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE_BYTES:
            raise NamingError('naming_file_bound_or_type')
        chunks = []
        size = 0
        while True:
            if time.monotonic() > deadline:
                raise NamingError('naming_scan_deadline')
            chunk = os.read(fd, min(65536, MAX_FILE_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_FILE_BYTES:
                raise NamingError('naming_file_bound_or_type')
        after = os.fstat(fd)
        if _identity(before) != _identity(after) or _identity(after) != _identity(path.lstat()):
            raise NamingError('naming_capture_changed')
        data = b''.join(chunks)
        return data, (str(path), _identity(after), hashlib.sha256(data).hexdigest())
    finally:
        os.close(fd)


def _scan(root, teams, deadline):
    subjects = {}
    definitions = {}
    facts = []
    total = 0
    # Reject symlinked canonical ancestors as well as symlinked entries.
    for parent in (root, root / 'org', root / 'org' / 'agents'):
        if parent.is_symlink():
            raise NamingError('naming_capture_symlink')
    for lifecycle, directory in (
        ('active', root / 'org' / 'agents'),
        ('pending', root / 'org' / 'agents' / '_pending'),
        ('terminated', root / 'org' / 'agents' / '_terminated'),
    ):
        try:
            before = directory.lstat()
        except FileNotFoundError:
            facts.append((str(directory), None))
            continue
        if not stat.S_ISDIR(before.st_mode):
            raise NamingError('naming_capture_directory')
        facts.append((str(directory), _identity(before)))
        selected = []
        with os.scandir(directory) as entries:
            for entry in entries:
                if time.monotonic() > deadline:
                    raise NamingError('naming_scan_deadline')
                if entry.name.startswith('.') or not entry.name.endswith('.md'):
                    continue
                if len(definitions) + len(selected) >= MAX_DEFINITIONS:
                    raise NamingError('naming_definition_bound')
                selected.append((entry.name[:-3], Path(entry.path)))
        for name, path in selected:
            data, fact = _bounded_file(path, deadline)
            total += len(data)
            if total > MAX_FILE_TOTAL:
                raise NamingError('naming_file_total_bound')
            agent = parse_agent_text(data.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n'), expected_name=name)
            if name.lower() == 'founder' or name in subjects:
                raise NamingError('naming_duplicate_or_founder_id')
            subjects[name] = lifecycle
            definitions[name] = agent
            facts.append(fact)
        if _identity(before) != _identity(directory.lstat()):
            raise NamingError('naming_capture_changed')
    team_path = root / 'org' / 'teams.yaml'
    data, fact = _bounded_file(team_path, deadline)
    total += len(data)
    if total > MAX_FILE_TOTAL:
        raise NamingError('naming_file_total_bound')
    facts.append(fact)
    import yaml
    raw = yaml.safe_load(data) or {}
    if not isinstance(raw, dict) or not isinstance(raw.get('teams', {}), dict):
        raise NamingError('naming_team_malformed')
    disk = TeamsRegistry._from_layout(raw.get('teams', {}), root)
    def layout(registry):
        return tuple((t, registry.manager_for_team(t)) for t in registry.teams())
    if layout(disk) != layout(teams):
        raise NamingError('naming_team_memory_disk_mismatch')
    members = {}
    for team in disk.teams():
        manager = disk.manager_for_team(team)
        for name, role in ((manager.name, 'manager'), *((w, 'worker') for w in manager.workers)):
            if name in members or subjects.get(name) not in ('active', 'pending'):
                raise NamingError('naming_team_membership_mismatch')
            definition = definitions[name]
            if definition.team != team or definition.role != role:
                raise NamingError('naming_team_membership_mismatch')
            members[name] = team
    if set(members) != {n for n, life in subjects.items() if life in ('active', 'pending')}:
        raise NamingError('naming_team_membership_mismatch')
    return Namespace(tuple(sorted(subjects.items())), tuple(sorted(facts, key=lambda x: x[0])))


def capture_namespace(root: Path, teams):
    deadline = time.monotonic() + SCAN_SECONDS
    first = _scan(root, teams, deadline)
    second = _scan(root, teams, deadline)
    if first != second or time.monotonic() > deadline:
        raise NamingError('naming_capture_changed')
    return second


def validate_rows(owner_rows, claim_rows, namespace=None):
    owners = {}
    claims = {}
    deadline = time.monotonic() + SCAN_SECONDS
    for row in owner_rows:
        if time.monotonic() > deadline:
            raise NamingError("naming_scan_deadline")
        kind, name, life, label, revision = row
        key = (kind, name)
        if (key in owners or kind not in ('agent', 'founder')
                or not isinstance(name, str) or _ID.fullmatch(name) is None
                or (kind == 'agent' and name == 'founder')
                or type(revision) is not int or revision <= 0
                or life not in ('active', 'pending', 'terminated', 'absent', 'founder')
                or (kind == 'founder' and (name != 'founder' or life != 'founder'))
                or (kind == 'agent' and life == 'founder')
                or (label is None and life != 'absent')
                or (label is not None and (not isinstance(label, str)
                    or (label != name and _LABEL.fullmatch(label) is None)))):
            raise NamingError('naming_owner_corrupt')
        owners[key] = row
    if ('founder', 'founder') not in owners:
        raise NamingError('naming_founder_missing')
    for row in claim_rows:
        if time.monotonic() > deadline:
            raise NamingError("naming_scan_deadline")
        token, kind, name, reserved, permanent = row
        key = (kind, name)
        owner = owners.get(key)
        if (owner is None or not isinstance(token, str) or token in claims
                or token != token.lower() or not token
                or (token != name.lower() and _LABEL.fullmatch(token) is None)
                or type(reserved) is not int or reserved not in (0, 1)
                or type(permanent) is not int or permanent not in (0, 1)
                or not (reserved or permanent)
                or (reserved and (owner[2] == 'absent' or token != name.lower()))
                or (token != name.lower() and not permanent)):
            raise NamingError('naming_claim_corrupt')
        claims[token] = row
    chosen_owners = {c[1:3] for c in claims.values() if c[4]}
    for key, (_, name, life, label, _) in owners.items():
        own_id = claims.get(name.lower())
        if life != 'absent' and (own_id is None or own_id[1:3] != key or own_id[3] != 1):
            raise NamingError('naming_live_id_missing')
        if label is not None:
            current = claims.get(label.lower())
            if current is None or current[1:3] != key or (label != name and not current[4]):
                raise NamingError('naming_current_claim_missing')
        if life == 'absent' and label is None and key in chosen_owners:
            raise NamingError('naming_absent_chosen_missing')
        if life == 'absent' and label == name and (own_id is None or own_id[1:3] != key or not own_id[4]):
            raise NamingError('naming_absent_default_corrupt')
    if namespace is not None:
        expected = dict(namespace.subjects)
        observed = {key[1]: row[2] for key, row in owners.items() if key[0] == 'agent' and row[2] != 'absent'}
        if expected != observed:
            raise NamingError('naming_projection_mismatch')
    return owners, claims


def _desired(owner_rows, claim_rows, namespace):
    owners, claims = validate_rows(owner_rows, claim_rows)
    expected = {('agent', name): life for name, life in namespace.subjects}
    expected[('founder', 'founder')] = 'founder'
    chosen_owners = {c[1:3] for c in claims.values() if c[4]}
    for key in set(owners) | set(expected):
        previous = owners.get(key)
        life = expected.get(key, 'absent')
        name = key[1]
        old_label = previous[3] if previous else None
        chosen = key in chosen_owners
        label = old_label if chosen else (None if life == 'absent' else name)
        revision = previous[4] if previous else 1
        if previous and (previous[2] != life or previous[3] != label):
            revision += 1
        owners[key] = (*key, life, label, revision)
    for token, claim in list(claims.items()):
        key = claim[1:3]
        reserved = int(owners[key][2] != 'absent' and token == key[1].lower())
        if not reserved and not claim[4]:
            del claims[token]
        else:
            claims[token] = (token, *key, reserved, claim[4])
    for key, life in expected.items():
        token = key[1].lower()
        existing = claims.get(token)
        if existing and existing[1:3] != key:
            raise NamingError('identity_name_unavailable')
        claims[token] = (token, *key, 1, existing[4] if existing else 0)
    validate_rows(tuple(owners.values()), tuple(claims.values()), namespace)
    if len(owners) > MAX_ROWS or len(claims) > MAX_ROWS:
        raise NamingError('naming_row_bound')
    return owners, claims


def reconcile_namespace(org, namespace, *, install=False):
    # Full reference admission is install-only; existing ID lifecycle paths do
    # not gain a whole-schema veto or an implicit workflow migration.
    references = None
    if install:
        from runtime.infrastructure.workflow_schema import validate_workflow_schema
        from runtime.orchestrator.authority import _release_schema_digest, _RELEASE_REFERENCE_HISTORIES
        with org.db.coherent_read_view() as conn:
            layout = validate_workflow_schema(conn, expected_org_slug=org.slug)
            version = validate_names(conn, org_slug=org.slug)
        # Temporary complete reference construction may do filesystem work.
        # It must finish before acquisition of the short org SQL transaction.
        references = tuple(_release_schema_digest(layout, history, version)
                           for history in _RELEASE_REFERENCE_HISTORIES)
        if 'unavailable' in references:
            raise NamingError('naming_release_reference_unavailable')
    with transaction(org.db) as conn:
        version = validate_names(conn, org_slug=org.slug)
        if install:
            validate_workflow_schema(conn, expected_org_slug=org.slug)
            raw = '\n'.join(str(r[0]) for r in conn.execute(
                'SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name'))
            if hashlib.sha256(raw.encode()).hexdigest() not in references:
                raise NamingError('naming_whole_database_mismatch')
        if not version:
            if not install:
                raise NamingError('naming_not_installed')
            execute_literal(conn, NAMING_DDL)
            conn.execute('INSERT INTO identity_name_schema VALUES (1,1,?)', (org.slug,))
            conn.execute('INSERT INTO identity_name_owners VALUES (?,?,?,?,?)',
                         ('founder', 'founder', 'founder', 'founder', 1))
            conn.execute('INSERT INTO identity_name_claims VALUES (?,?,?,?,?)',
                         ('founder', 'founder', 'founder', 1, 0))
        before_owners, before_claims = read_rows(conn)
        owners, claims = _desired(before_owners, before_claims, namespace)
        # Keep retained row identities; do not rewrite or replace unchanged rows.
        for row in before_claims:
            if row[0] not in claims:
                conn.execute('DELETE FROM identity_name_claims WHERE normalized_name=?', (row[0],))
        old_owners = {(r[0], r[1]): r for r in before_owners}
        for key, row in sorted(owners.items()):
            if key not in old_owners:
                conn.execute('INSERT INTO identity_name_owners VALUES (?,?,?,?,?)', row)
            elif row != old_owners[key]:
                conn.execute('UPDATE identity_name_owners SET lifecycle=?,current_label=?,revision=? WHERE kind=? AND canonical_id=?',
                             (row[2], row[3], row[4], *key))
        old_claims = {r[0]: r for r in before_claims}
        for token, row in sorted(claims.items()):
            if token not in old_claims:
                conn.execute('INSERT INTO identity_name_claims VALUES (?,?,?,?,?)', row)
            elif row != old_claims[token]:
                conn.execute('UPDATE identity_name_claims SET id_reserved=?,permanent=? WHERE normalized_name=?',
                             (row[3], row[4], token))
        validate_names(conn, org_slug=org.slug)
        validate_rows(*read_rows(conn), namespace)


def refresh_names(org, *, install=False):
    """Diagnostic projection; baseline same-ID outcomes remain their owner's."""
    org.naming_readiness = 'unavailable'
    try:
        snapshot = capture_namespace(org.root, org.teams)
        reconcile_namespace(org, snapshot, install=install)
    except (ValueError, OSError, UnicodeError) as exc:
        org.naming_diagnostic = getattr(exc, 'code', 'naming_canonical_unavailable')
        return False
    except Exception:
        org.naming_diagnostic = 'naming_database_unavailable'
        return False
    org.naming_diagnostic = None
    org.naming_readiness = 'ready'
    return True


def require_new_id(org, name):
    if not refresh_names(org):
        raise NamingError('naming_unavailable')
    with org.db.coherent_read_view() as conn:
        validate_names(conn, org_slug=org.slug)
        row = conn.execute('SELECT owner_kind,owner_id FROM identity_name_claims WHERE normalized_name=?', (name.lower(),)).fetchone()
        if row is not None and tuple(row) != ('agent', name):
            raise NamingError('identity_name_unavailable')


@asynccontextmanager
async def naming_writer(org, interval):
    """Inside the existing gate, outside its short canonical/profile leases."""
    refresh_names(org)
    try:
        yield interval
    finally:
        # Route cancellation has already drained _finish_consumer_write before
        # this synchronous terminal projection. No await and no new lock.
        refresh_names(org)


async def rename_owner(org, *, kind, canonical_id, label, expected_revision):
    editable_label(label)  # ID default exceptions never broaden editing.
    if type(expected_revision) is not int or expected_revision <= 0:
        raise NamingError('invalid_identity_revision')
    async with org.workflow_authority.async_writer_interval(publisher='identity_name_changed'):
        async with org.teams_lock:
            if not refresh_names(org):
                raise NamingError('naming_unavailable')
            snapshot = capture_namespace(org.root, org.teams)
            async with org.db_lock:
                with transaction(org.db) as conn:
                    validate_names(conn, org_slug=org.slug)
                    owners, claims = validate_rows(*read_rows(conn), snapshot)
                    key = (kind, canonical_id)
                    owner = owners.get(key)
                    if owner is None or owner[2] == 'absent':
                        raise NamingError('identity_owner_absent')
                    if owner[4] != expected_revision:
                        raise NamingError('stale_identity_revision')
                    if owner[3] == label:
                        return Subject(*owner, 'current')
                    token = label.lower()
                    claimed = claims.get(token)
                    if claimed is not None and claimed[1:3] != key:
                        raise NamingError('identity_name_unavailable')
                    if claimed is None:
                        conn.execute('INSERT INTO identity_name_claims VALUES (?,?,?,?,?)', (token, *key, 0, 1))
                    else:
                        conn.execute('UPDATE identity_name_claims SET permanent=1 WHERE normalized_name=?', (token,))
                    conn.execute('UPDATE identity_name_claims SET permanent=1 WHERE normalized_name=?', (owner[3].lower(),))
                    conn.execute('UPDATE identity_name_owners SET current_label=?,revision=revision+1 WHERE kind=? AND canonical_id=? AND revision=?',
                                 (label, *key, expected_revision))
                    payload = json.dumps({'org_slug': org.slug, 'kind': kind, 'canonical_id': canonical_id,
                                          'old_label': owner[3], 'new_label': label,
                                          'old_revision': expected_revision, 'new_revision': expected_revision + 1,
                                          'source': 'founder', 'actor': 'founder'}, sort_keys=True)
                    conn.execute('INSERT INTO audit_log(timestamp,task_id,agent,action,payload) VALUES (?,?,?,?,?)',
                                 (datetime.now(timezone.utc).isoformat(), 'founder', 'founder', 'identity_name_changed', payload))
                    validate_names(conn, org_slug=org.slug)
                    return Subject(kind, canonical_id, owner[2], label, expected_revision + 1, 'current')


def classify(org, token):
    """Typed internal lookup with actual ID precedence and fresh alias capture."""
    from runtime.orchestrator import prompt_loader
    from runtime.orchestrator._paths import OrgPaths
    if not isinstance(token, str) or not token.isascii():
        return None
    folded = token.lower()
    direct = None
    if folded == 'founder':
        direct = Subject('founder', 'founder', 'founder', None, None, 'id')
    elif _ID.fullmatch(folded):
        paths = OrgPaths(org.root)
        for lifecycle, loader in (('active', prompt_loader.load_agent),
                                  ('pending', prompt_loader.load_pending_agent),
                                  ('terminated', prompt_loader.load_terminated_agent)):
            if loader(paths, folded) is not None:
                direct = Subject('agent', folded, lifecycle, None, None, 'id')
                break
    if org.naming_readiness != 'ready':
        if direct is not None:
            return direct
        raise NamingError('naming_unavailable')
    try:
        # No stale cached alias; filesystem capture is outside the DB read lock.
        namespace = capture_namespace(org.root, org.teams)
        with org.db.coherent_read_view() as conn:
            if not validate_names(conn, org_slug=org.slug):
                raise NamingError('naming_not_installed')
            owners, claims = validate_rows(*read_rows(conn), namespace)
            if direct is not None:
                owner = owners.get((direct.kind, direct.canonical_id))
                if owner is None or owner[2] != direct.lifecycle:
                    raise NamingError('naming_projection_mismatch')
                return Subject(*owner, 'id')
            claim = claims.get(folded)
            if claim is None:
                return None
            owner = owners[claim[1:3]]
            category = 'current' if owner[2] != 'absent' and owner[3].lower() == folded else 'former'
            return Subject(*owner, category)
    except (ValueError, OSError, UnicodeError, sqlite3.DatabaseError):
        org.naming_readiness = 'unavailable'
        org.naming_diagnostic = 'naming_lookup_unavailable'
        if direct is not None:
            return direct
        raise NamingError('naming_unavailable') from None


class FormerNameError(NamingError):
    """Safe typed refusal, with current spelling but no forwarded recipient."""

    def __init__(self, address: str, subject: Subject) -> None:
        super().__init__('former_name')
        self.details = {'address': address, 'current_name': subject.current_label,
                        'canonical_id': subject.canonical_id, 'kind': subject.kind}


def classify_message_addresses(org: Any, tokens: list[str]) -> tuple[list[Subject | None], tuple | None]:
    """One read-only namespace capture before any message/attachment effect.

    Unknown message text is not a lookup error when names are available.
    When unavailable, preserve actual ID access, while non-ID addresses refuse
    with the existing org-local naming_unavailable policy. No reconciliation.
    """
    if not tokens:
        return [], None
    if getattr(org, 'naming_readiness', 'unavailable') != 'ready':
        return [classify(org, token) for token in tokens], None
    try:
        namespace = capture_namespace(org.root, org.teams)
        with org.db.coherent_read_view() as conn:
            if not validate_names(conn, org_slug=org.slug):
                raise NamingError('naming_not_installed')
            rows = read_rows(conn)
            owners, claims = validate_rows(*rows, namespace)
    except (ValueError, OSError, UnicodeError, sqlite3.DatabaseError):
        org.naming_readiness = 'unavailable'
        org.naming_diagnostic = 'naming_lookup_unavailable'
        return [classify(org, token) for token in tokens], None
    subjects = []
    for token in tokens:
        folded = token.lower()
        key = ('founder', 'founder') if folded == 'founder' else ('agent', folded)
        owner = owners.get(key)
        if owner is not None and owner[2] != 'absent':
            subject = Subject(*owner, 'id')
        else:
            claim = claims.get(folded)
            if claim is None:
                subject = None
            else:
                owner = owners[claim[1:3]]
                category = 'current' if owner[2] != 'absent' and owner[3].lower() == folded else 'former'
                subject = Subject(*owner, category)
        subjects.append(subject)
    # Check the whole input before eligibility/unknown fallback, including
    # mixed founder/current/unknown/former inputs.
    for token, subject in zip(tokens, subjects):
        if subject is not None and subject.classification == 'former':
            raise FormerNameError(token, subject)
    proof = tuple(tuple(sorted(tuple(row) for row in group)) for group in rows)
    return subjects, proof


def prepare_message_addresses(org: Any, body_markdown: str | None) -> MessageAddresses:
    from runtime.infrastructure.thread_mentions import MessageAddresses, parse_mentions
    tokens = parse_mentions(body_markdown)
    subjects, proof = classify_message_addresses(org, tokens)
    agents = tuple(dict.fromkeys(subject.canonical_id for subject in subjects
                                if subject is not None and subject.kind == 'agent'
                                and subject.lifecycle == 'active'))
    founder_only = bool(subjects) and all(subject is not None and subject.kind == 'founder'
                                         for subject in subjects)
    return MessageAddresses(agents, founder_only, proof)


def validate_message_addresses(org: Any, addresses: MessageAddresses | None) -> None:
    """DB-only recheck under the action's synchronization, no file capture."""
    if addresses is None or addresses.naming_rows is None:
        return
    with org.db.coherent_read_view() as conn:
        if not validate_names(conn, org_slug=org.slug):
            raise NamingError('naming_unavailable')
        rows = read_rows(conn)
        validate_rows(*rows)
        proof = tuple(tuple(sorted(tuple(row) for row in group)) for group in rows)
        if proof != addresses.naming_rows:
            raise NamingError('naming_classification_changed')


def read_name_metadata(org):
    """Read validated names without reconciliation or a new ID-read gate.

    An unavailable naming projection supplies no guessed/default label. The
    caller still owns canonical roster loading and its original error handling.
    """
    if getattr(org, 'naming_readiness', 'unavailable') != 'ready':
        return {}
    try:
        namespace = capture_namespace(org.root, org.teams)
        with org.db.coherent_read_view() as conn:
            if not validate_names(conn, org_slug=org.slug):
                return {}
            owners, _ = validate_rows(*read_rows(conn), namespace)
        return {key: Subject(*row, 'id') for key, row in owners.items()
                if row[2] != 'absent'}
    except Exception:
        # Naming-only storage/capture failure must not hide baseline ID reads.
        # No persistent write, repair, reconciliation or raw diagnostic leaks.
        return {}


def current_identity_records(org):
    """Canonical file roster plus optional validated label metadata.

    Retained absent/history-only owners are deliberately not enumerated.
    Definition revisions hash exact bytes, independently of name revisions.
    """
    from runtime.orchestrator import prompt_loader
    from runtime.orchestrator._paths import OrgPaths
    paths = OrgPaths(org.root)
    records = [(Subject('founder', 'founder', 'founder', None, None, 'id'), None)]
    for life, loader, directory in (
        ('active', prompt_loader.list_agents, paths.agents_dir),
        ('pending', prompt_loader.list_pending, paths.pending_agents_dir),
        ('terminated', prompt_loader.list_terminated, paths.agents_dir / '_terminated'),
    ):
        for definition in loader(paths):
            try:
                contents = (directory / f'{definition.name}.md').read_bytes()
            except FileNotFoundError:
                continue
            # Match ID and revision to one actual byte snapshot, even if a
            # canonical writer changed the definition after roster enumeration.
            parsed = parse_agent_text(contents.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n'),
                                      expected_name=definition.name)
            records.append((Subject('agent', parsed.name, life, None, None, 'id'),
                            hashlib.sha256(contents).hexdigest()))
    metadata = read_name_metadata(org)
    enriched = []
    for subject, revision in records:
        named = metadata.get((subject.kind, subject.canonical_id))
        enriched.append((named if named and named.lifecycle == subject.lifecycle else subject, revision))
    return enriched


def summary_name_metadata(metadata, canonical_id):
    """Additive roster fields; never replace a canonical name/key/revision."""
    subject = metadata.get(('agent', canonical_id))
    return {'addressable_name': subject.current_label if subject else None,
            'name_revision': subject.revision if subject else None,
            'naming_status': 'ready' if subject else 'unavailable'}
