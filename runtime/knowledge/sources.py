"""Non-migrating retired-source capture. Source text is untrusted data.

No stores, indexes, databases, provider sessions or generated instructions are
opened. Native scratch manifests supply path evidence only, never corpus text.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal, NoReturn

import yaml
from pydantic import BaseModel, Field

from runtime.infrastructure.learnings_store import (
    ID_RE, LIFECYCLE_VALUES, MAX_BODY_BYTES, PROVENANCE_VALUES, SCOPE_VALUES, SLUG_RE,
)
from runtime.orchestrator.agent_def import AgentParseError, parse_agent_text
from runtime.orchestrator.task_scratch import TaskScratchError, validate_task_scratch_manifest

SOURCE_ID = re.compile(r"[a-z0-9_]{1,64}\Z", re.ASCII)
REFERENCE_ID = re.compile(r"(?:runtime|claude)-[0-9a-f]{64}\Z", re.ASCII)
MAX_FILES = 4096
MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024


class SourceError(ValueError):
    """Closed diagnostic only: never echo source text or parser exceptions."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class Coverage(BaseModel):
    category: str
    state: Literal['included', 'excluded', 'unavailable']
    reason: str
    count: int | None = None  # null explicitly means unknown, never zero
    cutoff: str
    errors: list[str] = Field(default_factory=list)


class SourceRecord(BaseModel):
    reference_id: str
    category: Literal['runtime_memory', 'claude_memory']
    relative_path: str
    sha256: str
    body: str
    metadata: dict = Field(default_factory=dict)


class RetiredSource(BaseModel):
    source_id: str
    name: str
    team: str
    provider: str
    retirement: Literal['retained_definition_and_workspace'] = 'retained_definition_and_workspace'
    retired_at: str | None = None  # retirement does not persist a timestamp here
    cutoff: str
    consistent: bool = True
    capture_available: bool = True
    coverage: list[Coverage]
    records: list[SourceRecord]
    errors: list[str] = Field(default_factory=list)


class SourceReference(BaseModel):
    source_id: str
    reference_id: str
    available: bool
    reason: str | None = None
    record: SourceRecord | None = None
    coverage: list[Coverage]


def _signature(row: os.stat_result) -> tuple:
    return (row.st_dev, row.st_ino, row.st_mode, row.st_size,
            row.st_mtime_ns, row.st_ctime_ns, row.st_nlink)


@contextmanager
def _open(path: Path, *, directory: bool = False):
    """Walk every absolute component descriptor-relatively without aliases."""
    if not path.is_absolute() or '..' in path.parts:
        raise SourceError('unsafe_source_path')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for index, component in enumerate(path.parts[1:]):
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if index < len(path.parts) - 2 or directory:
                flags |= os.O_DIRECTORY
            try:
                child = os.open(component, flags, dir_fd=fd)
            except OSError as error:
                if error.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise SourceError('unsafe_source_path_or_alias') from None
                raise
            os.close(fd)
            fd = child
        row = os.fstat(fd)
        if (directory and not stat.S_ISDIR(row.st_mode)) or (
            not directory and (not stat.S_ISREG(row.st_mode) or row.st_nlink != 1)
        ):
            raise SourceError('unsafe_source_type_or_alias')
        yield fd
    finally:
        os.close(fd)


class _Observation:
    def __init__(self):
        self.facts: dict[Path, tuple | None] = {}
        self.inconsistent = False
        self.bytes = 0
        self.files = 0
        self.entries = 0

    def fail_inconsistent(self) -> NoReturn:
        # Optional-category handlers may wrap this error; never lose the fact.
        self.inconsistent = True
        raise SourceError('inconsistent_input')

    @contextmanager
    def opened(self, path: Path, *, directory: bool = False) -> Iterator[int]:
        try:
            with _open(path, directory=directory) as fd:
                yield fd
        except (OSError, SourceError) as error:
            if self.facts.get(path) is not None and (
                isinstance(error, OSError) or error.code.startswith('unsafe_source_')
            ):
                self.fail_inconsistent()
            raise

    def remember(self, path: Path, value: tuple | None) -> None:
        if path in self.facts and self.facts[path] != value:
            self.fail_inconsistent()
        self.facts[path] = value

    def listing(self, path: Path) -> list[str]:
        with self.opened(path, directory=True) as fd:
            before = _signature(os.fstat(fd))
            names = []
            with os.scandir(fd) as entries:
                for entry in entries:
                    names.append(entry.name)
                    self.entries += 1
                    if self.entries > MAX_FILES:
                        raise SourceError('source_inventory_limit')
            if before != _signature(os.fstat(fd)):
                self.fail_inconsistent()
            self.remember(path, before)
            return sorted(names)

    def exists(self, path: Path, *, directory: bool = False) -> bool:
        try:
            with self.opened(path, directory=directory) as fd:
                self.remember(path, _signature(os.fstat(fd)))
            return True
        except FileNotFoundError:
            self.remember(path, None)
            return False

    def read(self, path: Path) -> str:
        self.files += 1
        if self.files > MAX_FILES:
            raise SourceError('source_inventory_limit')
        with self.opened(path) as fd:
            before = _signature(os.fstat(fd))
            with os.fdopen(os.dup(fd), 'rb') as stream:
                raw = stream.read(MAX_FILE_BYTES + 1)
            self.bytes += len(raw)
            if len(raw) > MAX_FILE_BYTES or self.bytes > MAX_TOTAL_BYTES:
                raise SourceError('source_byte_limit')
            if before != _signature(os.fstat(fd)):
                self.fail_inconsistent()
            self.remember(path, before)
        try:
            return raw.decode('utf-8')
        except UnicodeError:
            raise SourceError('source_invalid_utf8') from None

    def verify(self) -> None:
        if self.inconsistent:
            self.fail_inconsistent()
        for path, expected in self.facts.items():
            try:
                # Directory metadata and negative observations are bookended too.
                with _open(path, directory=bool(expected and stat.S_ISDIR(expected[2]))) as fd:
                    actual = _signature(os.fstat(fd))
            except FileNotFoundError:
                actual = None
            except (OSError, SourceError):
                self.fail_inconsistent()
            if actual != expected:
                self.fail_inconsistent()


class _UniqueLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in result:
            raise SourceError('malformed_frontmatter')
        result[key] = loader.construct_object(value_node)
    return result


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def _frontmatter(text: str) -> tuple[dict, str]:
    match = re.fullmatch(r'---\r?\n(.*?)\r?\n---\r?\n(.*)', text, re.DOTALL)
    if match is None:
        raise SourceError('malformed_frontmatter')
    try:
        data = yaml.load(match[1], Loader=_UniqueLoader)
    except (yaml.YAMLError, ValueError, TypeError, RecursionError):
        raise SourceError('malformed_frontmatter') from None
    if not isinstance(data, dict):
        raise SourceError('malformed_frontmatter')
    return data, match[2].lstrip('\n')


def _memory(text: str, filename: str) -> tuple[dict, str]:
    data, body = _frontmatter(text)
    for key in ('id', 'slug', 'title', 'topic'):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise SourceError('memory_missing_' + key)
    if not ID_RE.fullmatch(data['id']) or not SLUG_RE.fullmatch(data['slug']):
        raise SourceError('memory_invalid_identity')
    if filename != f"{data['id']}-{data['slug']}.md":
        raise SourceError('memory_filename_identity_mismatch')
    for key, default, values in (
        ('lifecycle', 'valid', LIFECYCLE_VALUES),
        ('provenance', 'experiential', PROVENANCE_VALUES),
        ('scope', 'agent', SCOPE_VALUES),
    ):
        data.setdefault(key, default)
        if not isinstance(data[key], str) or data[key] not in values:
            raise SourceError('memory_invalid_' + key)
    for key in ('tags', 'related_to'):
        if key in data and (not isinstance(data[key], list) or
                            any(not isinstance(v, str) for v in data[key])):
            raise SourceError('memory_invalid_' + key)
    for key in ('authored_by', 'updated_by', 'source_task', 'supersedes', 'promoted_to'):
        if data.get(key) is not None and not isinstance(data[key], str):
            raise SourceError('memory_invalid_' + key)
    for key in ('authored_at', 'updated_at', 'last_verified'):
        value = data.get(key)
        if isinstance(value, (date, datetime)):
            value = value.isoformat()
        if value is not None:
            try:
                if not isinstance(value, str):
                    raise ValueError
                datetime.fromisoformat(value.replace('Z', '+00:00'))
            except ValueError:
                raise SourceError('memory_invalid_' + key) from None
        data[key] = value  # explicit unknown dates
    if 'salience' in data and type(data['salience']) is not int:
        raise SourceError('memory_invalid_salience')
    if not body.strip() or len(body.encode()) > MAX_BODY_BYTES:
        raise SourceError('memory_missing_or_oversized_body')
    # Preserve all recorded frontmatter, including promoted/supersession pointers.
    # Safe JSON projection rejects recursive/unsupported metadata rather than dropping it.
    try:
        data = json.loads(json.dumps(data, allow_nan=False))
    except (ValueError, TypeError, RecursionError):
        raise SourceError('memory_invalid_metadata') from None
    return data, body


def _record(category: str, relative: str, text: str, metadata: dict, body: str) -> SourceRecord:
    digest = hashlib.sha256(text.encode()).hexdigest()
    reference = hashlib.sha256((category + '\0' + relative + '\0' + digest).encode()).hexdigest()
    return SourceRecord(reference_id=('runtime-' if category == 'runtime_memory' else 'claude-') + reference,
                        category=category, relative_path=relative, sha256=digest,
                        metadata=metadata, body=body)


def _encoded(path: Path) -> str:
    return re.sub(r'[^A-Za-z0-9]', '-', str(path))


def _path_evidence(obs: _Observation, workspace: Path, original: Path) -> None:
    parent = workspace / '.happyranch' / 'task-scratch-manifests'
    if not obs.exists(parent, directory=True):
        raise SourceError('missing_path_evidence')
    names = [n for n in obs.listing(parent) if n.endswith('.json')]
    if not names:
        raise SourceError('missing_path_evidence')
    for name in names:
        try:
            data = json.loads(obs.read(parent / name))
            if not validate_task_scratch_manifest(
                data, expected_task_id=name[:-5],
                expected_root=original / '.happyranch' / 'task-tmp' / name[:-5],
            ):
                raise SourceError('unproven_retention')
        except FileNotFoundError:
            # The manifest was enumerated, so this is movement, not absence.
            obs.fail_inconsistent()
        except SourceError:
            raise
        except (ValueError, TaskScratchError, RecursionError):
            raise SourceError('ambiguous_source_attribution') from None


def _claude(obs: _Observation, root: Path, workspace: Path, source_id: str, home: Path) -> list[SourceRecord]:
    original = root / 'workspaces' / source_id
    _path_evidence(obs, workspace, original)
    encoded = _encoded(original)
    # Never enumerate provider projects. Inventory only this org's literal
    # candidate workspace/definition names, including strays and pending agents.
    checked: set[Path] = set()
    for parent in (root / 'org' / 'agents', root / 'org' / 'agents' / '_pending',
                   root / 'org' / 'agents' / '_terminated', root / 'workspaces',
                   root / 'workspaces' / '_terminated'):
        if not obs.exists(parent, directory=True):
            continue
        for name in obs.listing(parent):
            definition = parent.parent.name == 'agents' or parent.name == 'agents'
            candidate = name.removesuffix('.md') if definition else name
            if candidate in ('_pending', '_terminated'):
                continue
            if candidate != source_id and _encoded(root / 'workspaces' / candidate) == encoded:
                raise SourceError('ambiguous_source_attribution')
            if candidate != source_id and SOURCE_ID.fullmatch(candidate):
                retained = parent.name == '_terminated'
                peer = root / 'workspaces'
                if retained:
                    peer /= '_terminated'
                peer /= candidate
                if peer not in checked:
                    checked.add(peer)
                    try:
                        _path_evidence(obs, peer, root / 'workspaces' / candidate)
                    except SourceError as exc:
                        if exc.code not in ('missing_path_evidence', 'unproven_retention'):
                            # Retained paths outside today's provable org layout
                            # cannot be silently dropped from collision accounting.
                            raise SourceError('ambiguous_source_attribution') from None
    directory = home / '.claude' / 'projects' / encoded / 'memory'
    if not obs.exists(directory, directory=True):
        raise SourceError('missing_claude_directory')
    paths: list[Path] = []

    def inventory(parent: Path, depth: int = 0) -> None:
        if depth > 32:
            raise SourceError('source_depth_limit')
        for name in obs.listing(parent):
            path = parent / name
            try:
                row = path.lstat()
            except FileNotFoundError:
                obs.fail_inconsistent()
            if stat.S_ISLNK(row.st_mode):
                raise SourceError('unsafe_source_type_or_alias')
            if stat.S_ISDIR(row.st_mode):
                obs.remember(path, _signature(row))
                inventory(path, depth + 1)
            elif name.lower().endswith('.md'):
                # Establish availability BEFORE declaring the category included.
                if not obs.exists(path):
                    obs.fail_inconsistent()
                paths.append(path)
    inventory(directory)
    records = []
    for path in paths:
        try:
            text = obs.read(path)
            records.append(_record('claude_memory', path.relative_to(directory).as_posix(), text,
                                   {'authored_at': None, 'updated_at': None}, text))
        except (OSError, SourceError) as exc:
            code = exc.code if isinstance(exc, SourceError) else 'claude_entry_unreadable'
            raise SourceError('declared_claude_corpus_failed:' + code) from None
    return records


def capture_retired_source(root: Path, source_id: str) -> RetiredSource:
    """Capture a server-selected org source. No caller path or write capability."""
    if not SOURCE_ID.fullmatch(source_id) or source_id == 'founder':
        raise SourceError('unknown_retired_source')
    root = root.absolute()
    obs = _Observation()
    definitions = root / 'org' / 'agents'
    workspace = root / 'workspaces' / '_terminated' / source_id
    try:
        for active in (definitions / f'{source_id}.md', definitions / '_pending' / f'{source_id}.md',
                       root / 'workspaces' / source_id):
            # A workspace path is checked as a directory; any unsafe type refuses too.
            if obs.exists(active, directory=active.suffix != '.md'):
                raise SourceError('source_not_retired')
        text = obs.read(definitions / '_terminated' / f'{source_id}.md')
        _frontmatter(text)  # reject duplicate/invalid keys before the canonical parser
        agent = parse_agent_text(text.replace('\r\n', '\n'), expected_name=source_id)
        if agent.role != 'worker':
            raise SourceError('source_not_retired_worker')
        if not obs.exists(workspace, directory=True):
            raise SourceError('retained_workspace_missing')
    except FileNotFoundError:
        raise SourceError('unknown_retired_source') from None
    except (OSError, AgentParseError):
        raise SourceError('retained_identity_unreadable_or_invalid') from None
    cutoff = datetime.now(timezone.utc).isoformat()
    result = RetiredSource(source_id=source_id, name=agent.name, team=agent.team,
                           provider=agent.executor, cutoff=cutoff, coverage=[], records=[])

    def coverage(category, state, reason, count=None, errors=None):
        result.coverage.append(Coverage(category=category, state=state, reason=reason,
                                        count=count, cutoff=cutoff, errors=errors or []))

    try:
        directory = workspace / 'memory'
        if not obs.exists(directory, directory=True):
            legacy = obs.exists(workspace / 'learnings.md') or obs.exists(workspace / 'learnings', directory=True)
            raise SourceError('legacy_only_unavailable' if legacy else 'runtime_corpus_absent')
        names = [n for n in obs.listing(directory) if n.startswith(('MEM-', 'LRN-')) and n.endswith('.md')]
        records = []
        ids = set()
        # Inventory identities before any body processing so an in-place writer
        # between enumeration and a later read cannot evade the bookend.
        for name in names:
            try:
                if not obs.exists(directory / name):
                    obs.fail_inconsistent()
            except (OSError, SourceError) as exc:
                code = exc.code if isinstance(exc, SourceError) else 'runtime_entry_unreadable'
                raise SourceError(f'{code}:{name}') from None
        for name in names:
            try:
                text = obs.read(directory / name)
                metadata, body = _memory(text, name)
                number = metadata['id'].split('-', 1)[1]
                if number in ids:
                    raise SourceError('memory_duplicate_identity')
                ids.add(number)
                records.append(_record('runtime_memory', name, text, metadata, body))
            except (OSError, SourceError) as exc:
                code = exc.code if isinstance(exc, SourceError) else 'runtime_entry_unreadable'
                raise SourceError(f'{code}:{name}') from None
        result.records.extend(records)
        coverage('runtime_memory', 'included', 'verified_empty' if not names else 'all_structured_entries', len(names))
    except (OSError, SourceError) as exc:
        code = exc.code if isinstance(exc, SourceError) else 'runtime_corpus_unreadable'
        result.capture_available = False
        result.errors.append(code)
        coverage('runtime_memory', 'unavailable', code, errors=[code])
    try:
        records = _claude(obs, root, workspace, source_id, Path.home())
        result.records.extend(records)
        coverage('claude_memory', 'included', 'retained_path_evidence_and_collision_check', len(records))
    except (OSError, SourceError, RecursionError) as exc:
        code = exc.code if isinstance(exc, SourceError) else 'claude_corpus_unreadable'
        if code.startswith('declared_claude_corpus_failed:'):
            result.capture_available = False
            result.errors.append(code)
        coverage('claude_memory', 'unavailable', 'not captured — ' + code.replace('_', ' '), errors=[code])
    coverage('legacy_learnings', 'excluded', 'unsupported_without_migration')
    coverage('other_provider_memory', 'excluded', 'existence_and_retention_unverified')
    coverage('shared_kb_bodies', 'excluded', 'pointers_only_no_body_import')
    coverage('task_thread_history', 'excluded', 'no_unrestricted_history_ingestion')
    coverage('credentials_instructions_skills_sessions_caches', 'excluded', 'outside_declared_corpus')
    try:
        obs.verify()
    except SourceError:
        result.consistent = result.capture_available = False
        result.errors.append('inconsistent_input')
        result.records.clear()
        for category in result.coverage:
            if category.state == 'included':
                category.state = 'unavailable'
                category.reason = 'inconsistent_input'
                category.errors.append('inconsistent_input')
    return result


def capture_reference(root: Path, source_id: str, reference_id: str) -> SourceReference:
    if not REFERENCE_ID.fullmatch(reference_id):
        raise SourceError('invalid_source_reference')
    capture = capture_retired_source(root, source_id)
    record = next((r for r in capture.records if r.reference_id == reference_id), None)
    return SourceReference(source_id=source_id, reference_id=reference_id,
                           available=record is not None, record=record,
                           reason=None if record else 'reference_unavailable_or_changed', coverage=capture.coverage)
