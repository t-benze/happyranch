"""THR307 nightly disk sweep: 03:00 MACHINE-local, no startup catchup.

A single 1800s monotonic window includes discovery, ALL sequential orgs, both
phases, checks, Git and removal. No cursor/count cap or fairness guarantee.
All blocking work runs in an owned worker process group: cancellation of a
Python thread would not stop deletion. Public manual entry is DRY RUN ONLY:
    python -m runtime.daemon.disk_sweeper --dry-run --runtime-root /absolute/runtime
Default org disk_sweep.mode is dry_run; off does no inventory. No legacy cleanup
code/flags are invoked. Output is never a candidate. See the maintained THR307
spec for snapshot/process scope and nonpreemptible kernel-I/O limitations.
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
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from runtime.daemon._disk_sweep_fs import (
    Expired, Retain, allocated, check, directory, identity, mutate, names,
    process_use, signature, snapshot,
)

LOG = logging.getLogger(__name__)
BUDGET_SECONDS = 1800
TERMINAL = {'completed', 'failed', 'cancelled'}
TASK = re.compile(r'TASK-[0-9]+\Z')


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


def registrations(repo: Path, deadline: float) -> dict[str, dict]:
    raw = git(repo, deadline, 'worktree', 'list', '--porcelain', '-z')
    result = {}
    for block in raw.strip('\0').split('\0\0'):
        row = {}
        for field in block.split('\0'):
            key, _, value = field.partition(' ')
            row[key] = value
        path = row.get('worktree')
        if not path or path in result:
            raise Retain('unknown_git_registration')
        result[path] = row
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
    def __init__(self, org: Path, deadline: float):
        self.org, self.deadline = org, deadline

    def ownership(self, path: Path, workspace: Path, task_id: str) -> None:
        check(self.deadline)
        # Opening mode=ro cannot create/upgrade a production database.
        conn = sqlite3.connect((self.org / 'happyranch.db').as_uri() + '?mode=ro', uri=True, timeout=.1)
        conn.row_factory = sqlite3.Row
        conn.set_progress_handler(lambda: int(time.monotonic() >= self.deadline), 1000)
        try:
            conn.execute('BEGIN')
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
                # Terminal status does not establish ownership: native TEXT has
                # no task FK. Validate every owner's lineage before scope use.
                job_root = ancestry(row['task_id'])
                if row['status'] in ('pending', 'queued', 'running'):
                    if job_root == root:
                        raise Retain('active_job:' + row['status'])
                # Only native JobStatus terminal values are safe; TEXT storage
                # cannot rule out unsupported aliases or malformed values.
                elif row['status'] not in ('completed', 'failed', 'rejected'):
                    raise Retain('unknown_job_status')
        finally:
            conn.close()



def _worktree_gate(candidate: dict, deadline: float) -> tuple:
    path = Path(candidate["path"])
    task_id = path.name
    guard = read_json(path / '.worktree-guard.json', deadline, 4 * 1024 * 1024)
    if (not isinstance(guard, dict) or guard.get('task_id') != task_id
            or guard.get('worktree_root') != str(path)
            or guard.get('primary_root') != candidate['repo']):
        raise Retain('unknown_worktree_owner')
    repo = Path(candidate['repo'])
    row = registrations(repo, deadline).get(str(path))
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
    return row.get("HEAD"), row.get("branch"), common

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
    tree = snapshot(path, deadline)
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
    if candidate['phase'] == 'scratch' and any(Path(p).name == '.git' for p in tree):
        raise Retain('unknown_repository_in_scratch')
    process_use(path, deadline)
    # Re-read durable state after slow filesystem/process/Git observations.
    evidence.ownership(path, workspace, task_id)
    if candidate['phase'] == 'worktree' and _worktree_gate(candidate, deadline) != git_identity:
        raise Retain('git_identity_changed_during_admission')
    latest = snapshot(path, deadline)
    if {k: signature(v) for k, v in latest.items()} != {k: signature(v) for k, v in tree.items()}:
        raise Retain('tree_changed_during_admission')
    check(deadline)
    # Re-open without following ancestors; bind complete observed root identity.
    with directory(path, deadline) as fd:
        if signature(os.fstat(fd)) != signature(tree['']):
            raise Retain('identity_changed')
    return tree


def discover(org: Path, deadline: float):
    """Discovery completes before stable oldest-first ordering; partial is unknown."""
    candidates = []
    workspaces = org / 'workspaces'
    with directory(workspaces, deadline) as wfd:
        owners = names(wfd, deadline)
    for owner in owners:
        check(deadline)
        workspace = workspaces / owner
        with directory(workspace, deadline):
            pass
        tmp = workspace / '.happyranch/task-tmp'
        if tmp.exists() or tmp.is_symlink():
            with directory(tmp, deadline) as fd:
                for name in names(fd, deadline):
                    if TASK.fullmatch(name):
                        s = os.stat(name, dir_fd=fd, follow_symlinks=False)
                        candidates.append(dict(phase='scratch', path=str(tmp / name), workspace=str(workspace), oldest=s.st_mtime_ns))
        repos = workspace / 'repos'
        if not repos.exists():
            continue
        with directory(repos, deadline) as fd:
            repositories = names(fd, deadline)
        for name in repositories:
            repo = repos / name
            with directory(repo, deadline):
                pass
            # A non-Git repository directory is unknown, not an empty inventory.
            rows = registrations(repo, deadline)
            prefix = repo / '.claude/worktrees'
            for literal, row in rows.items():
                check(deadline)
                path = Path(literal)
                if path.parent != prefix or not TASK.fullmatch(path.name):
                    continue
                s = path.lstat() if path.exists() or path.is_symlink() else None
                candidates.append(dict(phase='worktree', path=str(path), workspace=str(workspace), repo=str(repo), oldest=s.st_mtime_ns if s else 0))
    return sorted(candidates, key=lambda c: (c['phase'] != 'scratch', c['oldest'], c['path']))


def atomic_json(path: Path, value: dict) -> None:
    # Summary only, never candidate/cursor state. Fixed owned daemon directory.
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + '.pending')
    fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.flush()
    os.replace(pending, path)


def _summarize_org(result: dict) -> None:
    candidates = result.get('candidates', [])
    result['measurement_scope'] = 'eligible scanned inodes only; retained/unreached allocation is unknown; removed allocation is not physical freed space'
    result['physical_freed_bytes'] = None
    result['retained_allocation_unknown_count'] = sum(c.get('allocated_bytes') is None for c in candidates)
    result['retained_count'] = sum(c.get('state') == 'retained' for c in candidates)
    result['partial_unknown_count'] = sum(c.get('state') in ('mutation_in_flight', 'partial_or_unknown') for c in candidates)
    result['retained_reasons'] = dict(Counter(c.get('reason', 'unknown').split(':', 1)[0] for c in candidates if c.get('state') in ('retained', 'partial_or_unknown')))
    result['known_eligible_leftovers'] = [c['path'] for c in candidates if c.get('state') == 'eligible']
    for phase, values in result.get('phases', {}).items():
        values['attribution_unknown'] = any(c.get('state') in ('mutation_in_flight', 'partial_or_unknown') for c in candidates if c['phase'] == phase)
    result['unknown_scope'] = {
        'discovery': result.get('discovery', 'unknown'), 'unreached': result.get('unreached'),
        'inspecting': [c['path'] for c in candidates if c.get('state') == 'inspecting'],
        'partial_mutation': [c['path'] for c in candidates if c.get('state') in ('mutation_in_flight', 'partial_or_unknown')],
    }


def sweep_org(org: Path, deadline: float, *, mode: str, checkpoint=lambda: None, now: float | None = None, result: dict | None = None, measurement_seen: set | None = None, removal_seen: set | None = None) -> dict:
    result = result if result is not None else {}
    result.update(org=org.name, mode=mode, outcome='incomplete', discovery='unknown', candidates=[], unreached=None,
                  phases={p: dict(measured_allocated_bytes=0, removed_attributable_bytes=0, attribution_unknown=False) for p in ('scratch', 'worktree')})
    if mode == 'off':
        result.update(outcome='off', discovery='not_performed')
        return result
    evidence = Evidence(org, deadline)
    measured_inodes = measurement_seen if measurement_seen is not None else set()
    removed_inodes = removal_seen if removal_seen is not None else set()

    def on_config():
        from runtime.orchestrator.org_config import load_org_config
        from runtime.orchestrator._paths import OrgPaths
        if load_org_config(OrgPaths(org)).disk_sweep_mode != 'on':
            raise Retain('configuration_no_longer_on')
    try:
        candidates = discover(org, deadline)
        result.update(discovery='complete', unreached=len(candidates))
        checkpoint()
        for candidate in candidates:
            check(deadline)
            result['candidates'].append(candidate)
            result['unreached'] -= 1
            candidate['state'] = 'inspecting'
            checkpoint()
            phase = result['phases'][candidate['phase']]
            mutation_started = False
            try:
                tree = admit(candidate, evidence, deadline, now=now)
                amount, _ = allocated(tree, measured_inodes)
                phase['measured_allocated_bytes'] += amount
                candidate['allocated_bytes'] = amount
                candidate['state'] = 'eligible'
                checkpoint()
                if mode != 'on':
                    continue
                # The entire admission is repeated immediately before mutation;
                # the first pass is inventory only, not reusable authority.
                # Checkpoint uncertainty BEFORE the final full fresh gates, so
                # writing a large summary cannot stale mutation admission.
                candidate['state'] = 'mutation_in_flight'
                phase['attribution_unknown'] = True
                checkpoint()
                on_config()
                fresh = admit(candidate, evidence, deadline, now=now)
                on_config()
                if {k: signature(v) for k, v in tree.items()} != {k: signature(v) for k, v in fresh.items()}:
                    raise Retain('tree_changed_before_mutation')
                mutation_started = True
                if candidate['phase'] == 'scratch':
                    mutate(Path(candidate['path']), fresh, deadline, remove=True)
                else:
                    repo, path = Path(candidate['repo']), Path(candidate['path'])
                    try:
                        git(repo, deadline, 'worktree', 'remove', str(path))
                    except Retain as exc:
                        candidate['first_remove_error'] = str(exc)
                        # Only one Git retry, with full fresh gates before
                        # contained repair AND again before Git retry. Own
                        # permission/partial changes count as recent and may
                        # conservatively refuse that retry.
                        on_config()
                        retry_tree = admit(candidate, evidence, deadline, now=now)
                        on_config()
                        repairs = mutate(path, retry_tree, deadline, remove=False)
                        admit(candidate, evidence, deadline, now=now, repairs=repairs)
                        on_config()
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
                if mutation_started:
                    candidate['state'] = 'partial_or_unknown'
                else:
                    candidate['state'] = 'retained'
                candidate['reason'] = str(exc) or type(exc).__name__
            checkpoint()
        result['outcome'] = 'error' if any(c['state'] == 'partial_or_unknown' for c in result['candidates']) else 'complete'
        # Git prune has repo-wide scope, with no exact-entry option. Never
        # prune if ANY missing/prunable/unknown registration could be affected.
        # No reclaimed-byte claim is made for registration maintenance.
        if mode == 'on':
            for repo in sorted({c['repo'] for c in candidates if 'repo' in c}):
                rows = registrations(Path(repo), deadline)
                # There is no path-selective Git prune. Only run its harmless
                # empty-admin form; ANY linked/unrelated/unknown record keeps
                # the whole repository's prune scope untouched.
                common = Path(git(Path(repo), deadline, 'rev-parse', '--path-format=absolute', '--git-common-dir').strip())
                admin = common / 'worktrees'
                with directory(common, deadline):
                    pass
                admin_empty = not os.path.lexists(admin)
                if not admin_empty:
                    with directory(admin, deadline) as fd:
                        admin_empty = not names(fd, deadline)
                if set(rows) != {repo} or not admin_empty:
                    result.setdefault('prune_retained', []).append(repo)
                    continue
                git(Path(repo), deadline, 'worktree', 'prune', '--expire=now')
    except Expired as exc:
        result.update(outcome='incomplete', error=str(exc))
        checkpoint()
        raise
    except Exception as exc:
        result.update(outcome='incomplete', error=str(exc) or type(exc).__name__)
    _summarize_org(result)
    checkpoint()
    return result


def sweep(roots: list[str], deadline: float, summary: Path, *, dry_run: bool, scheduled_date: str | None, revision: str, started_monotonic: float | None = None, runtime_root: Path | None = None) -> dict:
    from runtime.orchestrator.org_config import load_org_config
    from runtime.orchestrator._paths import OrgPaths
    report = dict(format='disk-sweep@1', revision=revision, scheduled_date=scheduled_date,
                  timezone=datetime.now().astimezone().tzname(), timezone_offset=datetime.now().astimezone().strftime('%z'),
                  started_at=datetime.now().astimezone().isoformat(), deadline_monotonic=deadline,
                  budget_seconds=deadline - started_monotonic if started_monotonic is not None else BUDGET_SECONDS, process_scope='visible Linux same-effective-UID processes, all threads; no exemptions; snapshot, not host-wide absence',
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
                cfg = load_org_config(OrgPaths(Path(root)))
                item['config_sha256'] = hashlib.sha256(config_path.read_bytes() if config_path.exists() else b'').hexdigest()
                item['configured_mode'] = cfg.disk_sweep_mode
                mode = 'off' if cfg.disk_sweep_mode == 'off' else 'dry_run' if dry_run else cfg.disk_sweep_mode
                sweep_org(Path(root), deadline, mode=mode, checkpoint=checkpoint, result=item, measurement_seen=measurement_seen, removal_seen=removal_seen)
            except Expired:
                raise
            except Exception as exc:
                item.update(outcome='error', error=str(exc) or type(exc).__name__)
            checkpoint()
        report['outcome'] = 'complete' if all(o['outcome'] in ('complete', 'off') for o in report['orgs']) else 'incomplete'
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


async def run_worker(roots: list[str], summary: Path, *, dry_run: bool, scheduled_date: str | None = None, budget: float = BUDGET_SECONDS, runtime_root: Path | None = None) -> dict:
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
            report = json.loads(summary.read_text())
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
            if marker.exists():
                old = json.loads(marker.read_text())
                if not isinstance(old.get('scheduled_date'), str):
                    raise ValueError('invalid disk sweep date metadata')
                if old['scheduled_date'] >= date:
                    return False
            atomic_json(marker, {'scheduled_date': date})
            return True
        try:
            if not await asyncio.to_thread(reserve):
                continue
            roots = [str(org.root) for _, org in sorted(state.orgs.items())]
            summary = data / f'{date}.json'
            cancelled = None
            try:
                report = await run_worker(roots, summary, dry_run=False, scheduled_date=date)
            except asyncio.CancelledError as exc:
                report = getattr(exc, 'disk_sweep_report', None)
                if report is None:
                    raise
                cancelled = exc
            if state.broken_orgs:
                report.update(outcome='incomplete', unloaded_orgs_unknown=sorted(state.broken_orgs))
                await asyncio.to_thread(atomic_json, summary, report)
            # Existing audit interface and row shape, one summary per swept org.
            # No notification interface suitable for a disk alert exists here;
            # below-20% context is carried in this audit/JSON, as approved.
            for slug, org in sorted(state.orgs.items()):
                item = next((r for r in report.get('orgs', []) if r['org'] == slug), None)
                if item is None or item.get('mode') != 'off':
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
        'org': item, 'unreached_orgs': report.get('unreached_orgs'),
        'interruption': report.get('interruption'),
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
        if item is not None and item.get('mode') == 'off':
            continue
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
    args = parser.parse_args()
    if args.worker:
        if args.summary is None or args.deadline is None:
            parser.error('worker requires deadline and summary')
        report = sweep(args.org_root, args.deadline, args.summary, dry_run=args.dry_run, scheduled_date=args.scheduled_date, revision=args.revision, started_monotonic=args.started_monotonic, runtime_root=args.runtime_root)
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
