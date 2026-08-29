# Host-session capacity rollout (6 task workers / 13 admissions)

This rollout is manual and restart-required. It does not change the provider
ceiling (8), thread workers (4), per-session enforcement envelopes, CPU quota,
or the aggregate `happyranch.slice`.

## Mutate configuration without dropping unrelated keys

Run from the repository root. Set `HAPPYRANCH_DAEMON_HOME` first when the
deployment does not use `~/.happyranch`.

```bash
CONFIG="${HAPPYRANCH_DAEMON_HOME:-$HOME/.happyranch}/config.yaml"
uv run python - "$CONFIG" 6 13 <<'PY'
import os, sys, tempfile, yaml
path, workers, cap = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
os.makedirs(os.path.dirname(path), exist_ok=True)
data = {}
if os.path.exists(path):
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
data["queue_workers"] = workers
data["host_global_session_cap"] = cap
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".config-", suffix=".yaml")
try:
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
finally:
    if os.path.exists(tmp):
        os.unlink(tmp)
PY
scripts/daemon.sh stop --force
scripts/daemon.sh start
```

The mapping round-trip preserves unrelated values (comments/formatting may be
normalized). Do not restart until the merge/deployment gate authorizes it.

## Live verification and rollback

Before and after restart, save authenticated `/api/v1/metrics` and public
`/api/v1/health` snapshots. Confirm settings reports `queue_workers=6` and
`host_global_session_cap=13`, both `restart_required=true`. On healthy Linux,
`host_sessions.admission.cap` must be 13; macOS/no-enforcement stays at 4.

Track `run_step_queue_depth`; admission active/queue depth/oldest wait/stall;
provider slot waits and 429 backoffs; aggregate `happyranch.slice`
`memory.current`/`memory.peak`; per-session `memory.high` and `memory.events`
`high`/`oom`/`oom_kill`; failures; admitted/released totals; cleanup status;
residue survivor count; and leftover cgroup units. No aggregate slice limit
or CPU quota should appear.

Rollback on sustained wait regression, unsafe aggregate memory, persistent
`high`, any new `oom`/`oom_kill`, cleanup/residue admission blocks, or higher
failure rate. Capture evidence, rerun the exact atomic mutation above with
arguments `4 11`, then perform the controlled stop/start. Verify healthy
Linux cap 11 (fallback 4), queues drain, admitted equals released after
quiescence, and no cgroup/unit residue remains. Restart recovery handles
interrupted durable work; never delete task rows or containment artifacts.
