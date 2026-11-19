"""Bounded advisory attribution of retained jobs to Linux flock holders.

No result from this module authorizes release, signals, adoption or recovery.
"""
from __future__ import annotations

import os
import stat
import sys
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from runtime.daemon.job_guardian import read_process_record, timeout_observed
from runtime.infrastructure.org_access_fence import org_access_key


class JobFenceHolder(BaseModel):
    job_id: str
    pid: int
    pgid: int
    age_seconds: float


class JobFenceObservation(BaseModel):
    status: Literal['observed', 'unavailable'] = 'unavailable'
    detail: str = 'fence observation unavailable; never proof the fence is free'
    holders: list[JobFenceHolder] = []
    exhausted_jobs: list[str] = []


def _read(path: Path, limit: int, deadline: float) -> str:
    if time.monotonic() >= deadline:
        raise ValueError('observation_budget_exhausted')
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('observation_bytes_exhausted')
    return raw.decode('ascii')


def observe_job_fences(home: Path, org_root: Path, job_ids: list[str] | None = None,
                       *, deadline: float | None = None) -> JobFenceObservation:
    """One sampled Linux observation, at most 1s/1024 jobs/65536 PIDs/4096 FDs.

    /proc/locks may still name the *dead acquiring daemon*, not its inheritor.
    Match device/inode there AND each recorded group's actual fdinfo flock.
    Legacy jobs without recorded identity are unavailable, never cleared.
    """
    result = JobFenceObservation()
    if job_ids is None:
        return result
    deadline = min(deadline, time.monotonic() + 1) if deadline is not None else time.monotonic() + 1
    try:
        if len(job_ids) > 1024:
            raise ValueError('observation_jobs_exhausted')
        for job_id in job_ids:
            if not isinstance(job_id, str) or not job_id.startswith('JOB-') or not job_id[4:].isdecimal():
                raise ValueError('job_identity_unavailable')
            if time.monotonic() >= deadline:
                raise ValueError('observation_budget_exhausted')
            if timeout_observed(str(org_root / 'jobs' / f'{job_id}.err')):
                result.exhausted_jobs.append(job_id)
        if sys.platform != 'linux':
            return result
        boot = _read(Path('/proc/sys/kernel/random/boot_id'), 128, deadline).strip()
        uptime = float(_read(Path('/proc/uptime'), 128, deadline).split()[0])
        ticks = os.sysconf('SC_CLK_TCK')
        info = (home / 'org-access-fences' / (org_access_key(org_root) + '.lock')).stat(follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('fence_identity_unavailable')
        lock_key = f'{os.major(info.st_dev):02x}:{os.minor(info.st_dev):02x}:{info.st_ino}'
        locks = _read(Path('/proc/locks'), 1024 * 1024, deadline)
        lock_seen = any('FLOCK' in line and lock_key in line.split() for line in locks.splitlines())
        groups: dict[int, dict] = {}
        missing = []
        for job_id in job_ids:
            try:
                record = read_process_record(org_root / 'jobs' / f'{job_id}.err.process.json', job_id)
            except (OSError, ValueError, RecursionError):
                missing.append(job_id)
                continue
            if record['boot_id'] != boot:
                continue  # Prior-boot identities cannot hold a current-boot FD.
            try:
                fields = _read(Path(f"/proc/{record['pid']}/stat"), 4096, deadline).rpartition(')')[2].split()
            except FileNotFoundError:
                fields = None
            if fields is not None:
                if int(fields[19]) == record['start_ticks']:
                    if int(fields[2]) != record['pgid'] or int(fields[3]) != record['sid']:
                        raise ValueError('job_identity_changed')
                elif int(fields[2]) == record['pgid'] or int(fields[3]) == record['sid']:
                    raise ValueError('job_identity_changed')
            if record['pgid'] in groups:
                raise ValueError('job_identity_ambiguous')
            groups[record['pgid']] = record
        fd_count = 0
        for count, entry in enumerate(Path('/proc').iterdir()):
            if count > 65536 or time.monotonic() >= deadline:
                raise ValueError('observation_budget_exhausted')
            if not entry.name.isdecimal():
                continue
            try:
                fields = _read(entry / 'stat', 4096, deadline).rpartition(')')[2].split()
                record = groups.get(int(fields[2]))
                if record is None or int(fields[3]) != record['sid']:
                    continue
                if int(fields[19]) < record['start_ticks']:
                    raise ValueError('job_identity_changed')
                for fd in (entry / 'fdinfo').iterdir():
                    fd_count += 1
                    if fd_count > 4096:
                        raise ValueError('observation_fds_exhausted')
                    text = _read(fd, 65536, deadline)
                    if any(line.startswith('lock:') and 'FLOCK' in line
                           and lock_key in line.split() for line in text.splitlines()):
                        result.holders.append(JobFenceHolder(job_id=record['job_id'], pid=int(entry.name),
                            pgid=record['pgid'], age_seconds=round(max(0, uptime-int(fields[19])/ticks), 3)))
                        break
            except (FileNotFoundError, ProcessLookupError):
                continue  # Vanishing processes/FDs provide no closure authority.
        result.status = 'unavailable' if missing else 'observed'
        names = '; '.join(f'{h.job_id} PID={h.pid} PGID={h.pgid} age={h.age_seconds}s' for h in result.holders)
        result.detail = ('writer_exclusion_unproven: ' + names if names else
                         'no matching recorded holder observed; never proof the fence is free')
        if not lock_seen:
            # Inherited flock can disappear from global /proc/locks when its
            # acquiring PID dies. Per-FD lock records above remain observable.
            result.detail += '; acquiring lock absent from /proc/locks'
        if missing:
            result.detail += '; identity unavailable: ' + ', '.join(missing)
    except (OSError, ValueError, IndexError, TypeError, KeyError, RecursionError) as exc:
        reason = str(exc) if type(exc) is ValueError else type(exc).__name__
        result.detail = 'fence observation unavailable: ' + reason + '; never proof the fence is free'
    return result
