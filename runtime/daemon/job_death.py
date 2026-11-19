"""Non-signalling, fail-closed Linux proof for retained job Stop only."""
from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

from runtime.daemon.job_fence_observation import observe_job_fences
from runtime.daemon.job_guardian import read_process_record


def prove_job_dead(home: Path, org_root: Path, job_id: str) -> dict | None:
    """A shared 2s deadline, 65536 proc entries and 16 MiB stat/boot bytes.

    Returns the exact identity to compare across admission and final recheck.
    No absence observation grants signalling, fence release or journal cleanup.
    """
    if sys.platform != 'linux' or not job_id.startswith('JOB-') or not job_id[4:].isdecimal():
        return None
    deadline = time.monotonic() + 2
    remaining = 16 * 1024 * 1024

    def read(path: Path, limit: int) -> str:
        nonlocal remaining
        if time.monotonic() >= deadline or remaining < limit + 1:
            raise ValueError('death_observation_exhausted')
        with path.open('rb') as stream:
            raw = stream.read(limit + 1)
        remaining -= len(raw)
        if len(raw) > limit or time.monotonic() >= deadline:
            raise ValueError('death_observation_exhausted')
        return raw.decode('ascii')

    def fields(pid: str) -> list[str]:
        raw = read(Path('/proc') / pid / 'stat', 4096)
        head, separator, tail = raw.rpartition(')')
        parts = tail.split()
        if not separator or head.split(' (', 1)[0] != pid or len(parts) < 20:
            raise ValueError('process_stat_unavailable')
        # Validate all fields consumed below even for unrelated processes.
        for index in (2, 3, 19):
            if int(parts[index]) < 0:
                raise ValueError('process_stat_unavailable')
        return parts

    try:
        path = org_root / 'jobs' / f'{job_id}.err.process.json'
        identity = read_process_record(path, job_id)
        boot = read(Path('/proc/sys/kernel/random/boot_id'), 128).strip()
        if str(uuid.UUID(boot)) != boot:
            return None
        if identity['boot_id'] == boot:
            try:
                leader = fields(str(identity['pid']))
            except (FileNotFoundError, ProcessLookupError):
                leader = None
            if leader is not None and int(leader[19]) == identity['start_ticks']:
                return None
            # A dead/reused leader is insufficient: descendants may retain
            # either the original process group OR the original session.
            for count, entry in enumerate(Path('/proc').iterdir()):
                if count >= 65536 or time.monotonic() >= deadline:
                    return None
                if not entry.name.isdecimal():
                    continue
                try:
                    member = fields(entry.name)
                except (FileNotFoundError, ProcessLookupError):
                    continue
                if int(member[2]) == identity['pgid'] or int(member[3]) == identity['sid']:
                    return None  # Includes zombies: no guessed reaping/exit evidence.
        observation = observe_job_fences(home, org_root, [job_id], deadline=deadline)
        if observation.status != 'observed' or observation.holders:
            return None
        if (read(Path('/proc/sys/kernel/random/boot_id'), 128).strip() != boot
                or read_process_record(path, job_id) != identity
                or time.monotonic() >= deadline):
            return None
        return identity
    except (OSError, ValueError, IndexError, TypeError, KeyError, RecursionError):
        return None
