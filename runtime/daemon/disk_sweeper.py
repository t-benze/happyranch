"""THR307 nightly disk sweep: 03:00 MACHINE-local, no startup catchup.

A single 1800s monotonic window includes discovery, ALL sequential orgs, both
phases, checks, Git and removal. No cursor/count cap or fairness guarantee.
All blocking work runs in an owned worker process group: cancellation of a
Python thread would not stop deletion. Public manual entry is DRY RUN ONLY:
    python -m runtime.daemon.disk_sweeper --dry-run --runtime-root /absolute/runtime
Scheduled deletion applies to every admitted org (THR307 seq134); obsolete org
disk_sweep input is ignored. Explicit manual dry run remains nonmutating. No
legacy cleanup code/flags are invoked. Output is never a candidate. See the
maintained THR307 spec for filesystem observation scope and nonpreemptible kernel-I/O limitations.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import signal
import sqlite3
import stat
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import ExitStack, closing, contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from runtime.daemon._disk_sweep_fs import (
    Expired, Retain, allocated, check, directory, identity, mutate, names,
    signature, snapshot, trees_equal,
)

LOG = logging.getLogger(__name__)
BUDGET_SECONDS = 1800
PROCESS_SCOPE = 'not checked under THR307 seq117; ended task and structural root required; no process absence or authentication claim'
TERMINAL = {'completed', 'failed', 'cancelled', 'superseded'}
TASK = re.compile(r'TASK-[0-9]+\Z')


class _RegistrationUnknown(Retain):
    """The entire repository registration scope is uncertain."""


def git(repo: Path, deadline: float, *args: str) -> str:
    check(deadline)
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0', LC_ALL='C')
    # Child remains in worker's process group, killed/reaped by supervisor.
    try:
        result = subprocess.run(
            ['git', '-c', 'core.fsmonitor=false', '-C', str(repo), *args], env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=max(.001, min(15, deadline - time.monotonic())),
        )
    except subprocess.TimeoutExpired as exc:
        # Abort the whole worker, so its supervisor kills any Git descendants.
        raise Expired("git subprocess deadline; descendant state unknown") from exc
    check(deadline)
    if result.returncode:
        raise Retain('git_failed:' + result.stderr.decode(errors='replace')[:1000])
    return result.stdout.decode('utf-8', errors='strict')


def _metadata_file(path: Path, deadline: float, *, text: bool = False) -> tuple:
    """Read metadata only through literal, no-follow parents and regular files."""
    check(deadline)
    try:
        with directory(path.parent, deadline) as parent:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            try:
                observed = os.fstat(fd)
                if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1:
                    raise Retain('unknown_metadata_type_or_hardlink')
                raw = os.read(fd, 65537 if text else 1)
                if text and (len(raw) > 65536 or len(raw) != observed.st_size or b'\0' in raw):
                    raise Retain('unknown_metadata_text')
                if signature(os.fstat(fd)) != signature(observed):
                    raise Retain('metadata_changed_during_read')
                check(deadline)
                return signature(observed), raw.decode('utf-8', errors='strict') if text else None
            finally:
                os.close(fd)
    except Expired:
        raise
    except Exception as exc:
        raise Retain(f'unknown_git_metadata:{path}: {exc}') from exc


def _registration_metadata(repo: Path, deadline: float) -> tuple[dict[str, Path], dict]:
    """Complete linked-admin census, including Git's reciprocal pointers.

    Git silently omits unreadable/missing gitdir records, so its exit0/list is
    never the census. No aliased entry, pointer or ancestor is read by Git.
    The stamp binds observations across Git reads and final action gates.
    """
    common, stamp, linked = repo / '.git', {}, {}
    admin = common / 'worktrees'
    try:
        absent = False
        with directory(common, deadline) as fd:
            stamp[str(common)] = signature(os.fstat(fd))
            # A primary common directory must not itself redirect Git.
            for name in ('commondir', 'gitdir'):
                if os.path.lexists(common / name):
                    raise Retain(f'unexpected_primary_pointer:{common / name}')
            try:
                os.stat('worktrees', dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                stamp[str(admin)] = None
                absent = True
        tree = {} if absent else snapshot(admin, deadline)
        for relative, observed in tree.items():
            check(deadline)
            path = admin / relative
            if stat.S_ISDIR(observed.st_mode):
                stamp[str(path)] = signature(observed)
            elif stat.S_ISREG(observed.st_mode):
                seen, _ = _metadata_file(path, deadline)
                if seen != signature(observed):
                    raise Retain(f'metadata_changed:{path}')
                stamp[str(path)] = seen
            else:
                raise Retain(f'aliased_or_special_metadata:{path}')
        entries = []
        if not absent:
            with directory(admin, deadline) as fd:
                entries = names(fd, deadline)
        for name in entries:
            check(deadline)
            entry = admin / name
            with directory(entry, deadline):
                pass
            _, gitdir = _metadata_file(entry / 'gitdir', deadline, text=True)
            _, commondir = _metadata_file(entry / 'commondir', deadline, text=True)
            _, head = _metadata_file(entry / 'HEAD', deadline, text=True)
            # Canonical absolute backlinks, and Git's ordinary ../.. common
            # pointer (or its exact absolute equivalent); no alias/.. spelling.
            gitdir = gitdir.removesuffix('\n')
            if not gitdir or gitdir != os.path.abspath(gitdir) or Path(gitdir).name != '.git':
                raise Retain(f'malformed_gitdir:{entry / "gitdir"}')
            path = Path(gitdir).parent
            if str(path) in linked or path == repo:
                raise Retain(f'conflicting_gitdir:{entry / "gitdir"}')
            if commondir not in ('../..\n', str(common) + '\n'):
                raise Retain(f'redirected_commondir:{entry / "commondir"}')
            if not (re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\n', head) or re.fullmatch(r'ref: refs/[^\s]+\n', head)):
                raise Retain(f'malformed_head:{entry / "HEAD"}')
            with directory(path, deadline) as fd:
                stamp[str(path)] = identity(os.fstat(fd))
            seen, backlink = _metadata_file(Path(gitdir), deadline, text=True)
            if backlink != 'gitdir: ' + str(entry) + '\n':
                raise Retain(f'conflicting_worktree_backlink:{gitdir}')
            stamp[gitdir] = seen
            linked[str(path)] = entry
        # A missing admin ENTRY is also invisible to Git. Reconcile literal
        # task worktree roots so their surviving backlinks cannot disappear
        # from the inventory. Never scan arbitrary repository children.
        prefix = repo / '.claude/worktrees'
        try:
            with directory(repo / '.claude', deadline) as fd:
                try:
                    os.stat('worktrees', dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    return linked, stamp
        except FileNotFoundError:
            return linked, stamp
        with directory(prefix, deadline) as fd:
            stamp[str(prefix)] = signature(os.fstat(fd))
            roots = names(fd, deadline)
        for name in roots:
            check(deadline)
            if TASK.fullmatch(name):
                path = prefix / name
                if str(path) not in linked:
                    raise Retain(f'unknown_unregistered_task_worktree:{path}')
        return linked, stamp
    except Expired:
        raise
    except Exception as exc:
        raise _RegistrationUnknown(f'unknown_git_registrations:{admin}: {exc}') from exc


def registrations(repo: Path, deadline: float, *, metadata: dict | None = None) -> dict[str, dict]:
    linked, before = _registration_metadata(repo, deadline)
    try:
        raw = git(repo, deadline, 'worktree', 'list', '--porcelain', '-z')
    except (Retain, UnicodeError) as exc:
        raise _RegistrationUnknown(f'unknown_git_registration_list:{repo}: {exc}') from exc
    result = {}
    for block in raw.strip('\0').split('\0\0'):
        row = {}
        for field in block.split('\0'):
            key, _, value = field.partition(' ')
            row[key] = value
        path = row.get('worktree')
        if not path or path in result:
            raise _RegistrationUnknown(f'unknown_git_registration:{repo}')
        result[path] = row
    if set(result) != {str(repo), *linked}:
        raise _RegistrationUnknown(f'unknown_git_registration_inventory:{repo / ".git/worktrees"}')
    for path in linked:
        if not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', result[path].get('HEAD', '')) or set(result[path]['HEAD']) == {'0'}:
            raise _RegistrationUnknown(f'unknown_git_registration_head:{linked[path]}')
    if _registration_metadata(repo, deadline) != (linked, before):
        raise _RegistrationUnknown(f'git_metadata_changed_during_inventory:{repo}')
    if metadata is not None:
        metadata.update(before)
    return result


def read_json(path: Path, deadline: float, limit: int = 65536):
    check(deadline)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        import stat
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise Retain('unknown_evidence_type')
        raw = os.read(fd, limit + 1)
        if len(raw) > limit:
            raise Retain('unknown_evidence_size')
        return json.loads(raw)
    finally:
        os.close(fd)


class Evidence:
    """Read-only native task/job/audit records. Never opens Database/schema."""
    def __init__(self, org: Path, deadline: float, *, writer_id: str | None = None, writing: bool = False):
        self.org, self.deadline = org, deadline
        self.writer_id, self.writing = writer_id, writing

    def _move_writer(self, conn: sqlite3.Connection) -> None:
        if not self.writing:
            return
        from types import SimpleNamespace
        from runtime.infrastructure.team_move_store import validate_team_move_schema
        from runtime.infrastructure.producer_admission import NativeWriterStore
        # No Database initializer or schema installation in this child. An
        # installed layout requires the actual parent's still-active global
        # reservation, not a flag or process-local coordinator in this worker.
        if not validate_team_move_schema(conn, org_slug=self.org.name):
            return
        writer = NativeWriterStore(SimpleNamespace(_conn=conn), self.org.name).history_uncommitted().get(self.writer_id)
        if (writer is None or writer.publisher != 'disk_sweep' or writer.state != 'active'
                or writer.agents or writer.teams):
            raise Retain('team_move_cleanup_owner_unavailable')
        if conn.execute('SELECT 1 FROM team_move_operations WHERE active_slot=1').fetchone():
            raise Retain('team_move_native_held')

    def ownership(self, path: Path, workspace: Path, task_id: str) -> None:
        check(self.deadline)
        # Opening mode=ro cannot create/upgrade a production database.
        conn = sqlite3.connect((self.org / 'happyranch.db').as_uri() + '?mode=ro', uri=True, timeout=.1)
        conn.row_factory = sqlite3.Row
        conn.set_progress_handler(lambda: int(time.monotonic() >= self.deadline), 1000)
        try:
            conn.execute('BEGIN')
            self._move_writer(conn)
            tasks = {}
            for row in conn.execute('SELECT id,parent_task_id,status,assigned_agent FROM tasks'):
                check(self.deadline)
                if row['id'] in tasks:
                    raise Retain('unknown_conflicting_lineage')
                tasks[row['id']] = dict(row)

            def ancestry(owner):
                seen = set()
                while True:
                    check(self.deadline)
                    if not isinstance(owner, str) or not owner:
                        raise Retain('unknown_malformed_job_or_task_lineage')
                    if owner in seen or owner not in tasks:
                        raise Retain('unknown_missing_or_cyclic_lineage')
                    seen.add(owner)
                    row = tasks[owner]
                    parent = row['parent_task_id']
                    if parent is None:
                        return owner
                    if not isinstance(parent, str) or not parent:
                        raise Retain('unknown_conflicting_lineage')
                    owner = parent

            root = ancestry(task_id)
            owner = tasks[task_id]
            if owner['assigned_agent'] != workspace.name:
                raise Retain('unknown_owner_workspace')
            for row in (owner, tasks[root]):
                if row['status'] not in TERMINAL:
                    raise Retain('task_or_root_not_terminal:' + str(row['status']))
            # Actual recorded workspace evidence, not TASK-like basename alone.
            observed = []
            for row in conn.execute("SELECT payload FROM audit_log WHERE task_id=? AND action='session_start'", (task_id,)):
                check(self.deadline)
                payload = json.loads(row[0])
                if not isinstance(payload, dict) or not isinstance(payload.get('workspace'), str):
                    raise Retain('unknown_workspace_evidence')
                observed.append(payload['workspace'])
            if not observed or set(observed) != {str(workspace)}:
                raise Retain('unknown_missing_or_conflicting_workspace_evidence')
            # A task/root scope includes all structurally related job owners.
            for row in conn.execute('SELECT task_id,status FROM jobs'):
                check(self.deadline)
                # Finished native jobs cannot keep historical owner lineage
                # live. Skip them BEFORE resolving their (possibly gone) owner.
                if row['status'] in ('completed', 'failed', 'rejected'):
                    continue
                if row['status'] not in ('pending', 'queued', 'running'):
                    raise Retain('unknown_job_status')
                job_root = ancestry(row['task_id'])
                if job_root == root:
                    raise Retain('active_job:' + row['status'])
        finally:
            conn.close()



def _retain_linked_descendants(path: Path, rows: dict[str, dict], deadline: float) -> None:
    """A complete reciprocal census protects every different linked descendant."""
    for registered in rows:
        check(deadline)
        descendant = Path(registered)
        if descendant != path and path in descendant.parents:
            raise Retain(f'linked_descendant:{descendant}')
    check(deadline)


def _worktree_gate(candidate: dict, deadline: float) -> tuple:
    path = Path(candidate["path"])
    task_id = path.name
    guard = read_json(path / '.worktree-guard.json', deadline, 4 * 1024 * 1024)
    if (not isinstance(guard, dict) or guard.get('task_id') != task_id
            or guard.get('worktree_root') != str(path)
            or guard.get('primary_root') != candidate['repo']):
        raise Retain('unknown_worktree_owner')
    repo = Path(candidate['repo'])
    metadata = {}
    rows = registrations(repo, deadline, metadata=metadata)
    _retain_linked_descendants(path, rows, deadline)
    row = rows.get(str(path))
    if row is None or 'locked' in row or 'prunable' in row:
        raise Retain('git_unregistered_locked_or_prunable')
    branch = row.get('branch')
    if branch is not None and branch != f'refs/heads/task/{task_id}':
        raise Retain('unknown_worktree_branch_owner')
    common = git(path, deadline, 'rev-parse', '--path-format=absolute', '--git-common-dir').strip()
    primary = git(repo, deadline, 'rev-parse', '--path-format=absolute', '--git-common-dir').strip()
    if common != primary or primary != str(repo / '.git') or git(path, deadline, 'rev-parse', '--show-toplevel').strip() != str(path):
        raise Retain('unknown_git_ownership')
    with directory(Path(common), deadline):
        pass
    dirty = git(path, deadline, 'status', '--porcelain=v1', '-z', '--untracked-files=all')
    if dirty:
        candidate['dirty_paths'] = dirty.split('\0')
        raise Retain('dirty_or_untracked')
    if git(path, deadline, 'rev-list', '-n1', 'HEAD', '--not', '--branches', '--remotes').strip():
        raise Retain('unique_head_commit')
    return row.get("HEAD"), row.get("branch"), common, metadata

def admit(candidate: dict, evidence: Evidence, deadline: float, *, now: float | None = None, repairs: dict | None = None) -> dict:
    """Fresh common gates before each candidate mutation/retry admission."""
    path, workspace = Path(candidate['path']), Path(candidate['workspace'])
    task_id = path.name
    if not TASK.fullmatch(task_id):
        raise Retain('unknown_task_identity')
    # Ancestor no-follow check also covers evidence sidecars' parents.
    with directory(workspace, deadline):
        pass
    evidence.ownership(path, workspace, task_id)
    if candidate['phase'] == 'scratch':
        from runtime.orchestrator.task_scratch import validate_task_scratch_manifest
        manifest = workspace / '.happyranch/task-scratch-manifests' / (task_id + '.json')
        with directory(manifest.parent, deadline):
            pass
        validate_task_scratch_manifest(read_json(manifest, deadline), expected_task_id=task_id, expected_root=path)
    else:
        git_identity = _worktree_gate(candidate, deadline)
    tree = snapshot(path, deadline, scratch=candidate['phase'] == 'scratch')
    import stat
    links = Counter(identity(s) for s in tree.values())
    if any(not stat.S_ISDIR(s.st_mode) and s.st_nlink > links[identity(s)] for s in tree.values()):
        raise Retain('unknown_external_hardlinks')
    cutoff = (time.time() if now is None else now) - 3600
    for relative, observed in tree.items():
        age_stat = observed
        if repairs and relative in repairs:
            repaired_signature, previous = repairs[relative]
            if signature(observed) != repaired_signature:
                raise Retain('tree_changed_after_owned_repair')
            # Only the precisely observed own chmod's ctime/mode is excluded;
            # file writes, additions and unknown directory changes still refuse.
            age_stat = previous
        if max(age_stat.st_mtime, age_stat.st_ctime) > cutoff:
            raise Retain('changed_within_hour')
    # Re-read durable state after slow filesystem/Git observations.
    evidence.ownership(path, workspace, task_id)
    if candidate['phase'] == 'worktree' and _worktree_gate(candidate, deadline) != git_identity:
        raise Retain('git_identity_changed_during_admission')
    latest = snapshot(path, deadline, scratch=candidate['phase'] == 'scratch')
    if not trees_equal(latest, tree, deadline):
        raise Retain('tree_changed_during_admission')
    check(deadline)
    # Re-open without following ancestors; bind complete observed root identity.
    with directory(path, deadline) as fd:
        if signature(os.fstat(fd)) != signature(tree['']):
            raise Retain('identity_changed')
    if candidate['phase'] == 'worktree' and _worktree_gate(candidate, deadline) != git_identity:
        raise Retain('git_identity_changed_before_action')
    return tree


@contextmanager
def _discovery_scope(path: Path, inventory: dict, deadline: float, checkpoint: Callable[[], None], *, missing_ok: bool = False) -> Iterator[None]:
    """Retain an uncertain literal scope; expiry always unwinds the batch."""
    try:
        check(deadline)
        yield
    except Expired as exc:
        # Nested scopes unwind; retain the narrowest interrupted path once.
        if not inventory['discovery_scopes'] or not inventory['discovery_scopes'][-1].get('interrupted'):
            inventory['discovery_scopes'].append(dict(path=str(path), reason=str(exc), candidate_count=None, interrupted=True))
        raise
    except FileNotFoundError as exc:
        if not missing_ok:
            inventory['discovery_scopes'].append(dict(path=str(path), reason=str(exc), candidate_count=None))
    except Exception as exc:
        inventory['discovery_scopes'].append(dict(path=str(path), reason=str(exc) or type(exc).__name__, candidate_count=None))
    finally:
        try:
            checkpoint()
        except Exception as exc:
            raise Expired('discovery checkpoint failed; global stop: ' + str(exc)) from exc


def discover(org: Path, deadline: float, *, inventory: dict | None = None, checkpoint: Callable[[], None] = lambda: None) -> list[dict]:
    """Stable ordering of known candidates; uncertain scopes are never empty."""
    inventory = inventory if inventory is not None else {}
    inventory.update(candidates=[], discovery_scopes=[], discovery='unknown', discovery_root=str(org / 'workspaces'))
    candidates = inventory['candidates']
    workspaces = org / 'workspaces'
    with _discovery_scope(workspaces, inventory, deadline, checkpoint):
        with directory(workspaces, deadline) as wfd:
            owners = names(wfd, deadline)
        for owner in owners:
            workspace = workspaces / owner
            with _discovery_scope(workspace, inventory, deadline, checkpoint):
                with directory(workspace, deadline):
                    pass
                tmp = workspace / '.happyranch/task-tmp'
                with _discovery_scope(tmp, inventory, deadline, checkpoint, missing_ok=True):
                    with directory(tmp, deadline) as fd:
                        for name in names(fd, deadline):
                            if TASK.fullmatch(name):
                                with _discovery_scope(tmp / name, inventory, deadline, checkpoint):
                                    s = os.stat(name, dir_fd=fd, follow_symlinks=False)
                                    candidates.append(dict(phase='scratch', path=str(tmp / name), workspace=str(workspace), oldest=s.st_mtime_ns, state='unreached'))
                repos = workspace / 'repos'
                with _discovery_scope(repos, inventory, deadline, checkpoint, missing_ok=True):
                    with directory(repos, deadline) as fd:
                        repositories = names(fd, deadline)
                    for name in repositories:
                        repo = repos / name
                        with _discovery_scope(repo, inventory, deadline, checkpoint):
                            with directory(repo, deadline):
                                pass
                            # Never walk arbitrary children or let Git find an
                            # ancestor repository. Only literal primary .git.
                            with directory(repo / '.git', deadline):
                                pass
                            rows = registrations(repo, deadline)
                            if str(repo) not in rows:
                                raise Retain('unknown_git_primary_registration')
                            prefix = repo / '.claude/worktrees'
                            for literal in rows:
                                check(deadline)
                                path = Path(literal)
                                if path.parent != prefix or not TASK.fullmatch(path.name):
                                    continue
                                with _discovery_scope(path, inventory, deadline, checkpoint):
                                    with directory(path.parent, deadline) as fd:
                                        try:
                                            s = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
                                        except FileNotFoundError:
                                            s = None
                                    candidates.append(dict(phase='worktree', path=str(path), workspace=str(workspace), repo=str(repo), oldest=s.st_mtime_ns if s else 0, state='unreached'))
    check(deadline)
    candidates.sort(key=lambda c: (c['phase'] != 'scratch', c['oldest'], c['path']))
    inventory['discovery'] = 'partial' if inventory['discovery_scopes'] else 'complete'
    return candidates


def _read_json(path: Path) -> dict:
    """Read reports/markers through literal parents and one regular inode."""
    with directory(path.parent, float('inf')) as parent:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd) as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise Retain('ambiguous_disk_sweep_metadata')
            value = json.load(stream)
            if (signature(os.fstat(stream.fileno())) != signature(before)
                    or signature(os.stat(path.name, dir_fd=parent, follow_symlinks=False)) != signature(before)):
                raise Retain('disk_sweep_metadata_changed')
            with directory(path.parent, float('inf')) as current:
                if identity(os.fstat(current)) != identity(os.fstat(parent)):
                    raise Retain('disk_sweep_parent_changed')
            return value


def atomic_json(path: Path, value: dict) -> None:
    # Every caller, including the schedule marker, uses literal parents. Never
    # create a missing scheduled control parent or truncate an unowned pending
    # inode (which could be a hardlink into a candidate). Ambiguity refuses.
    with directory(path.parent, float('inf')) as parent:
        def target_signature():
            try:
                observed = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return None
            if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1:
                raise Retain('ambiguous_disk_sweep_publication_target')
            return signature(observed)
        before = target_signature()
        pending = path.name + '.pending'
        fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        # Retain a pending file on failure as uncertainty; never retry or remove
        # someone else's pending evidence. Successful replace consumes ours.
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, sort_keys=True, indent=2)
            stream.flush()
            with directory(path.parent, float('inf')) as current:
                if identity(os.fstat(current)) != identity(os.fstat(parent)):
                    raise Retain('disk_sweep_publication_parent_changed')
            if (target_signature() != before
                    or signature(os.stat(pending, dir_fd=parent, follow_symlinks=False)) != signature(os.fstat(stream.fileno()))
                    or os.fstat(stream.fileno()).st_nlink != 1):
                raise Retain('disk_sweep_publication_changed')
            os.replace(pending, path.name, src_dir_fd=parent, dst_dir_fd=parent)


def _summarize_org(result: dict) -> None:
    candidates = result.get('candidates', [])
    result['measurement_scope'] = 'eligible scanned inodes only; retained/unreached allocation is unknown; removed allocation is not physical freed space'
    result['physical_freed_bytes'] = None
    result['process_scope'] = PROCESS_SCOPE
    # Compatibility field: unused since seq117 removed process inspection.
    result['process_exemptions'] = []
    result['retained_allocation_unknown_count'] = sum(c.get('allocated_bytes') is None for c in candidates)
    result['retained_count'] = sum(c.get('state') == 'retained' for c in candidates)
    result['partial_unknown_count'] = sum(c.get('state') in ('mutation_in_flight', 'partial_or_unknown') for c in candidates)
    result['retained_reasons'] = dict(Counter(c.get('reason', 'unknown').split(':', 1)[0] for c in candidates if c.get('state') in ('retained', 'partial_or_unknown')))
    result['known_eligible_leftovers'] = [c['path'] for c in candidates if c.get('state') == 'eligible']
    for phase, values in result.get('phases', {}).items():
        values['attribution_unknown'] = any(c.get('state') in ('mutation_in_flight', 'partial_or_unknown') for c in candidates if c['phase'] == phase)
    result['known_candidate_count'] = len(candidates)
    result['known_unreached_count'] = sum(c.get('state') == 'unreached' for c in candidates)
    # A known subset count must never stand in for an uncertain total.
    result['unreached'] = result['known_unreached_count'] if result.get('discovery') == 'complete' else None
    result['unknown_scope'] = {
        'discovery_scopes': result.get('discovery_scopes', []),
        'unfinished_discovery_root': result.get('discovery_root') if result.get('discovery') == 'unknown' else None,
        'discovery': result.get('discovery', 'unknown'), 'unreached': result.get('unreached'),
        'inspecting': [c['path'] for c in candidates if c.get('state') == 'inspecting'],
        'partial_mutation': [c['path'] for c in candidates if c.get('state') in ('mutation_in_flight', 'partial_or_unknown')],
    }


def sweep_org(org: Path, deadline: float, *, mode: str, checkpoint=lambda: None, now: float | None = None, result: dict | None = None, measurement_seen: set | None = None, removal_seen: set | None = None, writer_id: str | None = None) -> dict:
    result = result if result is not None else {}
    result.update(org=org.name, mode=mode, outcome='incomplete', discovery='unknown', candidates=[], unreached=None,
                  phases={p: dict(measured_allocated_bytes=0, removed_attributable_bytes=0, attribution_unknown=False) for p in ('scratch', 'worktree')})
    def report_checkpoint() -> None:
        _summarize_org(result)
        checkpoint()

    evidence = Evidence(org, deadline, writer_id=writer_id, writing=mode == 'on')
    measured_inodes = measurement_seen if measurement_seen is not None else set()
    removed_inodes = removal_seen if removal_seen is not None else set()
    uncertain_repos = set()

    try:
        candidates = discover(org, deadline, inventory=result, checkpoint=report_checkpoint)
        report_checkpoint()
        for candidate in candidates:
            check(deadline)
            candidate['state'] = 'inspecting'
            report_checkpoint()
            phase = result['phases'][candidate['phase']]
            mutation_started = False
            try:
                if candidate.get('repo') in uncertain_repos:
                    raise _RegistrationUnknown('unknown_repository_registration_scope')
                tree = admit(candidate, evidence, deadline, now=now)
                amount, _ = allocated(tree, measured_inodes)
                phase['measured_allocated_bytes'] += amount
                candidate['allocated_bytes'] = amount
                candidate['state'] = 'eligible'
                report_checkpoint()
                if mode != 'on':
                    continue
                # The entire admission is repeated immediately before mutation;
                # the first pass is inventory only, not reusable authority.
                # Checkpoint uncertainty BEFORE the final full fresh gates, so
                # writing a large summary cannot stale mutation admission.
                candidate['state'] = 'mutation_in_flight'
                phase['attribution_unknown'] = True
                report_checkpoint()
                fresh = admit(candidate, evidence, deadline, now=now)
                if not trees_equal(tree, fresh, deadline):
                    raise Retain('tree_changed_before_mutation')
                if candidate['phase'] == 'scratch':
                    mutation_started = True
                    mutate(Path(candidate['path']), fresh, deadline, remove=True, scratch=True)
                else:
                    repo, path = Path(candidate['repo']), Path(candidate['path'])
                    # Last local metadata check after fresh tree comparisons;
                    # Git must never consume a known redirected registration.
                    _retain_linked_descendants(path, registrations(repo, deadline), deadline)
                    mutation_started = True
                    try:
                        git(repo, deadline, 'worktree', 'remove', str(path))
                    except Retain as exc:
                        candidate['first_remove_error'] = str(exc)
                        # Only one Git retry, with full fresh gates before
                        # contained repair AND again before Git retry. Own
                        # permission/partial changes count as recent and may
                        # conservatively refuse that retry.
                        retry_tree = admit(candidate, evidence, deadline, now=now)
                        _retain_linked_descendants(path, registrations(repo, deadline), deadline)
                        repairs = mutate(path, retry_tree, deadline, remove=False)
                        admit(candidate, evidence, deadline, now=now, repairs=repairs)
                        _retain_linked_descendants(path, registrations(repo, deadline), deadline)
                        candidate['retry_count'] = 1
                        git(repo, deadline, 'worktree', 'remove', str(path))
                    if str(path) in registrations(repo, deadline):
                        raise Retain('registration_remains_after_remove')
                if os.path.lexists(candidate['path']):
                    raise Retain('root_remains_after_remove')
                _, attributable = allocated(fresh, removed_inodes)
                phase['removed_attributable_bytes'] += attributable
                candidate.update(state='removed', removed_attributable_bytes=attributable)
                # Earlier partial operations remain unknown.
                phase['attribution_unknown'] = any(c.get('state') in ('partial_or_unknown', 'mutation_in_flight') for c in result['candidates'] if c['phase'] == candidate['phase'])
            except Expired:
                raise
            except Exception as exc:
                if isinstance(exc, _RegistrationUnknown):
                    repo = candidate['repo']
                    if repo not in uncertain_repos:
                        result['discovery_scopes'].append(dict(path=repo, reason=str(exc), candidate_count=None))
                        uncertain_repos.add(repo)
                    result['discovery'] = 'partial'
                if mutation_started:
                    candidate['state'] = 'partial_or_unknown'
                else:
                    candidate['state'] = 'retained'
                candidate['reason'] = str(exc) or type(exc).__name__
            report_checkpoint()
        result['outcome'] = ('error' if any(c['state'] == 'partial_or_unknown' for c in candidates)
                             else 'complete' if result['discovery'] == 'complete' else 'incomplete')
        # Git prune has repo-wide scope, with no exact-entry option. Never
        # prune if ANY missing/prunable/unknown registration could be affected.
        # No reclaimed-byte claim is made for registration maintenance.
        if mode == 'on' and result['discovery'] == 'complete':
            for repo in sorted({c['repo'] for c in candidates if 'repo' in c}):
                try:
                    rows = registrations(Path(repo), deadline)
                except _RegistrationUnknown as exc:
                    result['discovery_scopes'].append(dict(path=repo, reason=str(exc), candidate_count=None))
                    result.update(discovery='partial', outcome='incomplete')
                    break
                # There is no path-selective Git prune. Only run its harmless
                # empty-admin form; ANY linked/unrelated/unknown record keeps
                # the whole repository's prune scope untouched.
                # The reconciled no-follow census proves there are no admin
                # entries as well as no linked rows when only primary remains.
                if set(rows) != {repo}:
                    result.setdefault('prune_retained', []).append(repo)
                    continue
                # Prune also writes agent repository metadata; primary-only
                # registration does not waive native move writer ownership.
                with closing(sqlite3.connect((org / 'happyranch.db').as_uri() + '?mode=ro', uri=True, timeout=.1)) as conn:
                    conn.row_factory = sqlite3.Row
                    conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
                    conn.execute('BEGIN')
                    evidence._move_writer(conn)
                    check(deadline)
                git(Path(repo), deadline, 'worktree', 'prune', '--expire=now')
    except Expired as exc:
        result.update(outcome='incomplete', error=str(exc))
        result['candidates'].sort(key=lambda c: (c['phase'] != 'scratch', c['oldest'], c['path']))
        report_checkpoint()
        raise
    except Exception as exc:
        result.update(outcome='incomplete', error=str(exc) or type(exc).__name__)
    _summarize_org(result)
    checkpoint()
    return result


def sweep(roots: list[str], deadline: float, summary: Path, *, dry_run: bool, scheduled_date: str | None, revision: str, started_monotonic: float | None = None, runtime_root: Path | None = None, native_writers: dict[str, str] | None = None) -> dict:
    from runtime.orchestrator.org_config import load_org_config
    from runtime.orchestrator._paths import OrgPaths
    report = dict(format='disk-sweep@1', revision=revision, scheduled_date=scheduled_date,
                  timezone=datetime.now().astimezone().tzname(), timezone_offset=datetime.now().astimezone().strftime('%z'),
                  started_at=datetime.now().astimezone().isoformat(), deadline_monotonic=deadline,
                  budget_seconds=deadline - started_monotonic if started_monotonic is not None else BUDGET_SECONDS,
                  process_scope=PROCESS_SCOPE,
                  outcome='incomplete', orgs=[], unreached_orgs=list(sorted(roots)))
    def checkpoint():
        atomic_json(summary, report)
    checkpoint()
    measurement_seen, removal_seen = set(), set()
    if runtime_root is not None:
        report['runtime_discovery'] = 'unknown'
        checkpoint()
        try:
            with directory(runtime_root / 'orgs', deadline) as fd:
                roots = [str(runtime_root / 'orgs' / name) for name in names(fd, deadline) if name not in ('_pending', '_archive')]
            report.update(runtime_discovery='complete', unreached_orgs=roots.copy())
        except Exception as exc:
            report.update(error=str(exc), unreached_orgs=None)
            checkpoint()
            return report
    report['selected_org_roots'] = roots.copy()
    checkpoint()
    try:
        for root in sorted(roots):
            check(deadline)
            item = dict(org=Path(root).name, outcome='incomplete')
            report['orgs'].append(item)
            report['unreached_orgs'].remove(root)
            checkpoint()
            try:
                config_path = Path(root) / 'org/config.yaml'
                load_org_config(OrgPaths(Path(root)))
                item['config_sha256'] = hashlib.sha256(config_path.read_bytes() if config_path.exists() else b'').hexdigest()
                # Policy is independent of org config; loading above preserves
                # unrelated validation. Only explicit preview suppresses writes.
                mode = 'dry_run' if dry_run else 'on'
                sweep_org(Path(root), deadline, mode=mode, checkpoint=checkpoint, result=item, measurement_seen=measurement_seen, removal_seen=removal_seen,
                          writer_id=(native_writers or {}).get(root))
            except Expired:
                raise
            except Exception as exc:
                item.update(outcome='error', error=str(exc) or type(exc).__name__)
            checkpoint()
        report['outcome'] = 'complete' if all(o['outcome'] == 'complete' for o in report['orgs']) else 'incomplete'
    except Expired as exc:
        report['error'] = str(exc)
    report['finished_at'] = datetime.now().astimezone().isoformat()
    try:
        space = os.statvfs(summary.parent)
        report['disk_free_percent_context_only'] = 100 * space.f_bavail / space.f_blocks if space.f_blocks else None
        report['low_disk_below_20_percent'] = report['disk_free_percent_context_only'] is not None and report['disk_free_percent_context_only'] < 20
    except OSError:
        report['disk_free_percent_context_only'] = None
    checkpoint()
    return report


async def run_worker(roots: list[str], summary: Path, *, dry_run: bool, scheduled_date: str | None = None, budget: float = BUDGET_SECONDS, runtime_root: Path | None = None, native_writers: dict[str, str] | None = None) -> dict:
    """Supervise entire process group, including Git, until stopped/reaped.

    SIGKILL at the shared deadline (no deletion grace/window extension).
    A task in uninterruptible kernel I/O can delay reaping; this OS limitation
    is not presented as a hard real-time guarantee. No detached worker survives
    ordinary cancellation and the daemon waits for group termination.
    """
    started_monotonic = time.monotonic()
    deadline = started_monotonic + budget
    revision = await asyncio.to_thread(_revision)
    argv = [sys.executable, '-m', 'runtime.daemon.disk_sweeper', '--worker', '--summary', str(summary), '--deadline', str(deadline), '--started-monotonic', str(started_monotonic), '--revision', revision]
    for root in roots:
        argv += ['--org-root', root]
    for root, writer_id in sorted((native_writers or {}).items()):
        argv += ['--native-writer', root, writer_id]
    if runtime_root is not None:
        argv += ['--runtime-root', str(runtime_root)]
    if dry_run:
        argv.append('--dry-run')
    if scheduled_date:
        argv += ['--scheduled-date', scheduled_date]
    initial = dict(format='disk-sweep@1', revision=revision, orgs=[], unreached_orgs=roots,
                   runtime_discovery='unknown' if runtime_root else 'selected_daemon_orgs',
                   outcome='incomplete', scheduled_date=scheduled_date, budget_seconds=budget,
                   started_at=datetime.now().astimezone().isoformat(),
                   timezone=datetime.now().astimezone().tzname(),
                   timezone_offset=datetime.now().astimezone().strftime('%z'))
    def preview_parents():
        # Public preview remains usable with no control directory. Creation is
        # manual-only and descriptor-relative; aliases still refuse before write.
        for component in reversed((summary.parent, *summary.parent.parents)):
            if component == component.parent:
                continue
            with directory(component.parent, deadline) as parent:
                try:
                    os.mkdir(component.name, 0o700, dir_fd=parent)
                except FileExistsError:
                    pass
                with directory(component, deadline):
                    pass
    if dry_run:
        await asyncio.to_thread(preview_parents)
    await asyncio.to_thread(atomic_json, summary, initial)
    spawn = asyncio.create_task(asyncio.create_subprocess_exec(
        *argv, start_new_session=True, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    cause = None
    cancelled = False
    try:
        process = await asyncio.shield(spawn)
    except asyncio.CancelledError:
        # A cancellation during spawn still obtains and stops the real child;
        # never lose its handle while it might begin deletion.
        process = await spawn
        cause, cancelled = 'shutdown', True
    except Exception as exc:
        initial.update(error='worker_spawn:' + type(exc).__name__)
        await asyncio.to_thread(atomic_json, summary, initial)
        return initial
    try:
        if not cancelled:
            await asyncio.wait_for(process.wait(), max(.001, deadline - time.monotonic()))
        if process.returncode:
            cause = f'worker_exit:{process.returncode}'
    except asyncio.TimeoutError:
        cause = 'deadline'
    except asyncio.CancelledError:
        cause, cancelled = 'shutdown', True
    finally:
        # Reconcile the entire group even if its leader exited first.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()
    def finish():
        try:
            report = _read_json(summary)
        except Exception:
            report = dict(format='disk-sweep@1', orgs=[], unreached_orgs=roots, revision=revision)
        for item in report.get('orgs', []):
            _summarize_org(item)
        report.update(worker_pid=process.pid, worker_group_stop='owned group signalled SIGKILL when present; leader reaped; uninterruptible kernel I/O remains an OS limitation')
        if cause:
            report.update(outcome='incomplete', interruption=cause, partial_mutation='unknown', finished_at=datetime.now().astimezone().isoformat())
        atomic_json(summary, report)
        return report
    report = await asyncio.to_thread(finish)
    if cancelled:
        error = asyncio.CancelledError()
        error.disk_sweep_report = report
        raise error
    return report


def _scheduled_admission(deadline: float) -> dict:
    """Observe the daemon-wide stop once, before any scheduled writer work.

    Only a missing literal file or the exact Boolean false allows admission.
    This is an ordinary bounded filesystem observation, not an in-flight fence.
    """
    from runtime.runtime import daemon_home
    path = daemon_home() / 'disk-sweep/stop.json'
    admission = dict(scope='daemon', path=str(path), state='suspended', reason='invalid_control')
    try:
        with directory(path.parent, deadline) as parent:
            parent_identity = identity(os.fstat(parent))
            try:
                before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                # ENOENT on this final literal name only; parent failure is not
                # absence. Reopen parents to reject aliases/replacement.
                with directory(path.parent, deadline) as current:
                    if identity(os.fstat(current)) != parent_identity:
                        raise Retain('stop_parent_changed')
                check(deadline)
                return dict(admission, state='allowed', reason='absent')
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise Retain('stop_not_regular_or_hardlinked')
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            try:
                if signature(os.fstat(fd)) != signature(before):
                    raise Retain('stop_changed_before_read')
                raw = os.read(fd, 65537)
                if len(raw) > 65536 or len(raw) != before.st_size:
                    raise Retain('stop_oversized_or_short_read')
                if signature(os.fstat(fd)) != signature(before):
                    raise Retain('stop_changed_during_read')
                if signature(os.stat(path.name, dir_fd=parent, follow_symlinks=False)) != signature(before):
                    raise Retain('stop_replaced_during_read')
                with directory(path.parent, deadline) as current:
                    if identity(os.fstat(current)) != parent_identity:
                        raise Retain('stop_parent_changed')
                check(deadline)
            finally:
                os.close(fd)
        # Retain duplicate keys as pairs, so even duplicate identical false
        # values cannot silently pass the closed one-key object contract.
        pairs = json.loads(raw.decode('utf-8', errors='strict'), object_pairs_hook=lambda value: value)
        if (not isinstance(pairs, list) or len(pairs) != 1
                or not isinstance(pairs[0], tuple) or pairs[0][0] != 'stop'
                or type(pairs[0][1]) is not bool):
            raise Retain('stop_requires_only_boolean_stop')
        check(deadline)
        return dict(admission, state='suspended' if pairs[0][1] else 'allowed',
                    reason='stop_true' if pairs[0][1] else 'stop_false')
    except Exception as exc:
        return dict(admission, error=f'{type(exc).__name__}: {exc}')


async def _run_scheduled_worker(state, summary: Path, date: str) -> dict:
    from runtime.orchestrator.producer_move import native_writer
    from runtime.orchestrator.team_move import DeferredTeamMove
    started = time.monotonic()
    started_at = datetime.now().astimezone().isoformat()
    deadline = started + BUDGET_SECONDS
    admission = await asyncio.to_thread(_scheduled_admission, deadline)
    report = dict(format='disk-sweep@1', revision=_revision(), scheduled_date=date,
                  timezone=datetime.now().astimezone().tzname(), timezone_offset=datetime.now().astimezone().strftime('%z'),
                  started_at=started_at, deadline_monotonic=deadline, budget_seconds=BUDGET_SECONDS,
                  process_scope=PROCESS_SCOPE, outcome='incomplete', orgs=[],
                  unreached_orgs=[str(org.root) for _, org in sorted(state.orgs.items())],
                  scheduled_admission=admission)
    if admission['state'] == 'suspended':
        report.update(outcome='suspended', finished_at=datetime.now().astimezone().isoformat())
        LOG.warning('Disk sweep globally suspended: %s', admission)
    # Publish the actual initial summary before ANY durable native reservation.
    # An unsafe target/pending inode must refuse while all orgs are unstarted.
    # Worker checkpoints still publish independently; a later failure retains
    # genuine reservation uncertainty rather than claiming this check fences it.
    await asyncio.to_thread(atomic_json, summary, report)
    if admission['state'] == 'suspended':
        return report
    # Reservations span filesystem/process work; their short gate/transaction
    # does not. Retain active evidence on process loss; D reads the same history.
    with ExitStack() as stack:
        roots, writers, dispositions, deferred = [], {}, {}, {}
        for slug, org in sorted(state.orgs.items()):
            check(deadline)
            try:
                disposition = stack.enter_context(native_writer(org.db, publisher='disk_sweep'))
            except DeferredTeamMove as exc:
                deferred[slug] = exc.code
                continue
            root = str(org.root)
            roots.append(root)
            dispositions[slug] = disposition
            if disposition.writer_id is not None:
                writers[root] = disposition.writer_id
        # Any unexpected unwind, missing report or interrupted worker remains
        # unknown. Only affirmative complete native closure below may settle.
        for disposition in dispositions.values():
            disposition.uncertain = True
        check(deadline)
        report = await run_worker(roots, summary, dry_run=False, scheduled_date=date,
                                  native_writers=writers, budget=deadline - time.monotonic())
        report['scheduled_admission'] = admission
        if deferred:
            report.update(outcome='incomplete', move_deferred_orgs=deferred)
        await asyncio.to_thread(atomic_json, summary, report)
        for slug, disposition in dispositions.items():
            items = [item for item in report.get('orgs', []) if item.get('org') == slug]
            if (len(items) == 1
                    and items[0].get('mode') == 'on'
                    and items[0].get('outcome') == 'complete'
                    and not report.get('interruption')
                    and report.get('worker_group_stop')
                    and not any(c.get('state') in ('mutation_in_flight', 'partial_or_unknown')
                                for c in items[0].get('candidates', []))):
                disposition.uncertain = False
        return report


def _revision() -> str:
    # Build/source identity without running Git on the event loop. Hash the
    # actual executing modules, works in installed wheels too (no .git needed).
    digest = hashlib.sha256()
    for path in (Path(__file__), Path(__file__).with_name('_disk_sweep_fs.py'), Path(__file__).with_name('app.py'), Path(__file__).parents[1] / 'orchestrator/org_config.py'):
        digest.update(path.read_bytes())
    return 'source-sha256:' + digest.hexdigest()


def next_night(now: datetime) -> datetime:
    """Next future local 03:00; never startup catchup. OS local TZ, not org TZ."""
    local = now.astimezone()
    date = local.date() + timedelta(days=local.hour >= 3)
    # Naive local timestamp lets the OS resolve the date's DST offset.
    return datetime.combine(date, datetime.min.time()).replace(hour=3).astimezone()


async def disk_sweeper_loop(state) -> None:
    from runtime.runtime import daemon_home
    while True:
        due = next_night(datetime.now().astimezone())
        await asyncio.sleep(max(0, due.timestamp() - time.time()))
        if state.runtime is None:
            continue
        data = daemon_home() / 'disk-sweep'
        marker = data / 'last-scheduled.json'
        date = due.date().isoformat()
        def reserve():
            # Mark BEFORE launching. An interrupted night's partial run is not
            # retried after restart. Corrupt metadata refuses, never duplicates.
            # Validate literal parents BEFORE interpreting final-file ENOENT.
            # No marker/control directory creation on the scheduled path.
            with directory(data, float('inf')):
                try:
                    old = _read_json(marker)
                except FileNotFoundError:
                    old = None
                else:
                    if not isinstance(old, dict):
                        raise ValueError('invalid disk sweep date metadata')
            if old is not None:
                if not isinstance(old.get('scheduled_date'), str):
                    raise ValueError('invalid disk sweep date metadata')
                if old['scheduled_date'] >= date:
                    return False
            atomic_json(marker, {'scheduled_date': date})
            return True
        try:
            if not await asyncio.to_thread(reserve):
                continue
            summary = data / f'{date}.json'
            cancelled = None
            try:
                report = await _run_scheduled_worker(state, summary, date)
            except asyncio.CancelledError as exc:
                report = getattr(exc, 'disk_sweep_report', None)
                if report is None:
                    raise
                cancelled = exc
            if state.broken_orgs:
                report.update(unloaded_orgs_unknown=sorted(state.broken_orgs))
                if report.get('outcome') != 'suspended':
                    report['outcome'] = 'incomplete'
                await asyncio.to_thread(atomic_json, summary, report)
            # Existing audit interface and row shape, one summary per swept org.
            # No notification interface suitable for a disk alert exists here;
            # below-20% context is carried in this audit/JSON, as approved.
            for slug, org in sorted(state.orgs.items()):
                item = next((r for r in report.get('orgs', []) if r['org'] == slug), None)
                await asyncio.to_thread(org.db.insert_audit_log, '', 'daemon', 'disk_sweep_summary', _audit_payload(report, item, summary))
            if cancelled is not None:
                raise cancelled
        except asyncio.CancelledError:
            raise
        except Exception:
            LOG.exception('Disk sweep incomplete; no same-night automatic retry')


def _audit_payload(report: dict, item: dict | None, summary: Path) -> dict:
    return {
        'summary_path': str(summary), 'revision': report.get('revision'),
        'scheduled_date': report.get('scheduled_date'),
        'timezone': report.get('timezone'), 'timezone_offset': report.get('timezone_offset'),
        'started_at': report.get('started_at'), 'finished_at': report.get('finished_at'),
        'budget_seconds': report.get('budget_seconds'), 'outcome': report.get('outcome'),
        'scheduled_admission': report.get('scheduled_admission'),
        'org': item, 'unreached_orgs': report.get('unreached_orgs'),
        'interruption': report.get('interruption'),
        'move_deferred_orgs': report.get('move_deferred_orgs'),
        'low_disk_below_20_percent': report.get('low_disk_below_20_percent'),
    }


def _manual_audit(report: dict, roots: list[str], summary: Path) -> None:
    """Reuse the existing writer without initializing/upgrading any schema."""
    import threading
    # Import defines the facade required by the unchanged writer decorator;
    # no Database instance, initializer, OrgState or schema installer is called.
    import runtime.infrastructure.database  # noqa: F401
    from runtime.infrastructure.db.audit import AuditMixin

    class SummaryWriter(AuditMixin):
        def __init__(self, conn):
            self._conn = conn
            self._lock = threading.RLock()

    for root in roots:
        item = next((o for o in report.get('orgs', []) if o['org'] == Path(root).name), None)
        conn = sqlite3.connect((Path(root) / 'happyranch.db').as_uri() + '?mode=rw', uri=True, timeout=.1)
        try:
            SummaryWriter(conn).insert_audit_log('', 'daemon', 'disk_sweep_summary', _audit_payload(report, item, summary))
        finally:
            conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--runtime-root', type=Path)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--org-root', action='append', default=[], help=argparse.SUPPRESS)
    parser.add_argument('--summary', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--deadline', type=float, help=argparse.SUPPRESS)
    parser.add_argument('--revision', help=argparse.SUPPRESS)
    parser.add_argument('--started-monotonic', type=float, help=argparse.SUPPRESS)
    parser.add_argument('--scheduled-date', help=argparse.SUPPRESS)
    parser.add_argument('--native-writer', nargs=2, action='append', default=[], help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        if args.summary is None or args.deadline is None:
            parser.error('worker requires deadline and summary')
        report = sweep(args.org_root, args.deadline, args.summary, dry_run=args.dry_run, scheduled_date=args.scheduled_date, revision=args.revision, started_monotonic=args.started_monotonic, runtime_root=args.runtime_root,
                       native_writers=dict(args.native_writer))
    else:
        if not args.dry_run or args.runtime_root is None:
            parser.error('manual use requires --dry-run --runtime-root ABSOLUTE_RUNTIME')
        from runtime.runtime import RuntimeDir, daemon_home
        runtime = RuntimeDir.load(args.runtime_root)
        summary = daemon_home() / 'disk-sweep' / f'manual-{time.time_ns()}.json'
        # Candidate/org enumeration itself belongs inside the shared budget.
        report = asyncio.run(run_worker([], summary, dry_run=True, runtime_root=runtime.root))
        _manual_audit(report, report.get('selected_org_roots', []), summary)
        print(json.dumps({'summary_path': str(summary), 'report': report}, indent=2))
    return 0 if report.get('outcome') == 'complete' else 1


if __name__ == '__main__':
    raise SystemExit(main())
