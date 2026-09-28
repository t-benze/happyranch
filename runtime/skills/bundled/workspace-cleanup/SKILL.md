---
name: workspace-cleanup
description: Shared daily/manual own-workspace cleanup contract — bounded, Git-aware, non-force reclamation of registered non-primary task worktrees and their dependency caches, with an exact same-user process observation and authoritative terminal/history/peer joins.
---

# workspace-cleanup

The one shared workspace-cleanup skill for every agent, including no-repo
agents. It is invoked two ways, and both use exactly this contract:

- **Manual dispatch.** The task brief's first line is exactly:

  ```
  HAPPYRANCH SYSTEM WORKSPACE CLEANUP RUN (manual-dispatch)
  ```

  A manual request that does not carry that exact first line is
  **inventory-only**: report candidates and skips, and take no action. Manual
  runs never advance the scheduled occurrence count.

- **Daemon daily trigger.** The daemon's daily marker is exactly:

  ```
  HAPPYRANCH SYSTEM WORKSPACE CLEANUP RUN (daemon-triggered)
  ```

  The daemon composes the brief that begins with that marker, so the same skill
  and the same gates apply automatically.

Both markers identify the same skill. No other skill, task, or prompt grants
cleanup authority.

## Scope

You may only ever consider paths inside **your own workspace**:
`<workspace>/repos/<repo>/.claude/worktrees/*` (registered linked worktrees) and
the literal `node_modules`/`.venv` caches inside them. Never touch another
agent's workspace, the primary checkout, workspace roots, `output/`, `memory/`,
artifact stores, configuration, databases, logs, canonical skills, or any
unknown path. Never use elevation.

## Report-only ordinal

The first **two** triggered cleanups for an agent are strictly report-only: no
deletion, no move, no modification. Only from the third triggered run, and only
when every gate below is re-derived at action time, may you act. Manual dispatch
contributes **zero** to that count; a manual run still requires the two prior
joined terminal daemon occurrences before it may act.

## Authoritative joins (R5 / R6.5 / R7)

Authoritative recorded terminal status plus a fresh, complete, current scan of
folder use replaces separate live-session/task-to-process identity for terminal
candidates, terminal cleanup peers, and the two prior joined terminal scheduled
occurrences. Nonterminal peers block. Missing, conflicting, or incomplete
non-exempt evidence is a skip. Positive use blocks.

- **R5 (target task).** The owning task must be terminal and older than the
  retention floor (24 hours for a cache, 7 days for a whole worktree). The
  owning task and its terminal time come from the authoritative read-only
  `happyranch recall` record; the shared OS UID and any directory mtime are
  never used to establish the owner or the terminal age.
- **R6.5 (cleanup peers).** Union all same-owner cleanup peers across **all**
  statuses from a complete keyset-paged assigned-task lookup — the manual
  marker, the daemon marker, and trigger-history rows — and omit only your own
  acting task. Any nonterminal peer is a skip. Recheck after claim and again
  immediately before every action. Unrelated audit history, however large,
  never makes this candidate-specific join incomplete.
- **R7 (prior occurrences).** Require exactly two distinct prior joined terminal
  scheduled occurrences. Duplicates collapse. The filtered trigger audit is
  read through complete keyset pages; missing, malformed, changing, or failed
  pages are a skip.
- **Cooperative overlap.** Perform the peer and listing recheck after claim and
  immediately before each action. This is cooperative, not a lock. A new peer
  appearing in the window makes the action a skip.

## Ownership and protected paths

Only a **registered, non-primary, clean** whole worktree whose `HEAD` is durably
preserved, with no open or closed-unmerged PR for its branch, and no protection,
may be considered. Durable preservation is exactly one of: the existing
accepted durable ref (normally `origin/main`); a freshly verified matching
remote task branch on `origin`; or one unambiguous confirmed merged PR whose
task branch and head match. Missing, stale, unfetchable, mismatched, conflicting,
or malformed remote/PR evidence refuses. A confirmed merged PR preserves the
integrated content but may not preserve the original commit topology. Refuse
cross-owner, protected, symlink/shared/unknown, dirty whole worktrees,
local-only or unreachable commits, and insufficient age. Never rewrite, move,
archive, bundle, tag, quarantine, or repair preserved work.

The sole dirty-tree exception is a literal immediate-child `node_modules` or
`.venv` cache. Its containing worktree may be dirty, but every other gate still
applies and the action may remove only that cache. The dirty whole-worktree is
never removed or cleaned. Source bytes and Git status must be identical before
and after the cache action.

## Current-use observation

Before acting, run the bundled read-only helper **only through a task-bound,
host-visible HappyRanch job**. Never run it directly in the cleanup session and
never fall back to an in-session scan. A whole-worktree candidate is passed as
its own target. A literal `node_modules`/`.venv` cache candidate MUST
also resolve to its containing **registered** worktree so that use anywhere in that
worktree (for example a process whose `cwd` is a sibling `src/`) blocks even
though the cache directory itself is untouched:

The job carries the literal, shell-quoted command for the existing packaged
`scripts/check_path_use.py`, the active cleanup task/session binding, and a
unique receipt nonce. Submit it with the single-line `happyranch jobs submit
--from-file` contract, wait with the same task/session binding, then consume
`jobs show` and `jobs output`. Authenticate the exact job id, task, agent,
session-bearing rationale, nonce/title, interpreter, workspace cwd, script,
fresh submission time, terminal status, exit code, complete stdout/stderr, and
scanner JSON. Only exact `completed` + exit `0` + empty stderr + matching target
+ `clear_observation` continues. Rejected, failed, timeout, output-cap, nonzero,
stale, mismatched, malformed, or missing output refuses without mutation.

For a whole-worktree candidate the containing worktree is the candidate itself,
so `--containing-worktree` may be omitted. For a cache candidate the helper
derives the unique actual registered worktree root (never a nested directory
such as `web/`). When supplied explicitly, it must match that canonical root.
Missing, ambiguous, unregistered, or changed registration is `unknown`, never a
`dirname` fallback.

It returns exactly one of `clear_observation`, `blocked`, or `unknown`
(never `safe`), and exit `0` only for `clear_observation`.

- **Complete same-user population.** Every process running as your user must be
  read, except confirmed-exited processes and the fixed login/session daemons
  matched by an **exact readable process name AND its exact bounded cgroup
  role**:
  - `sshd-session` in `session-<N>.scope` under your user slice;
  - `systemd` (the per-user manager) and `(sd-pam)` in
    `user@<UID>.service/init.scope`;
  - `ssh-agent` in `user@<UID>.service/app.slice/ssh-agent.service`;
  - `gpg-agent` in `user@<UID>.service/app.slice/gpg-agent.service`;
  - `gcr-ssh-agent` or its `ssh-agent` child alias in
    `user@<UID>.service/app.slice/gcr-ssh-agent.service`.
- Name alone, role alone, a unit/session lookalike, a generic
  `*.service`/`*agent` wildcard, an arbitrary cgroup, or a different UID never
  qualifies. A qualifying exception is deliberately **not inspected**:
  unreadable `exe`/namespace does not veto it, and it is not proof the service
  cannot use the path.
- Root-owned processes are outside the scan — never permission to run as root.
- Any **other** unreadable same-user process makes coverage `unknown` ->
  skip. A readable occupied non-exempt member is `blocked`. Positive use
  blocks even when coverage is otherwise incomplete.
- An independently absent PID/TID `stat` confirms that sampled process or
  thread exited. If the corresponding `stat` remains readable, a missing,
  unreadable, or incomplete `status` is an incomplete identity -> `unknown`,
  not confirmed exit. An unparseable starttime, PID/TID reuse, or changed
  real/effective/saved/fs credentials is also `unknown`.
- Per thread, the helper checks `cwd`, `root`, `exe`, `maps`, and the private
  FD table, and verifies cross-mount-namespace path identity. Every newly
  admitted read/iteration — including each maps line and each FD entry — is
  admitted against one shared deadline; an already admitted syscall is not
  hard-preempted, and a scan that exhausts the deadline never claims success.

This is a **snapshot** with a disclosed later-opener/write-interruption and
data-loss residual risk. It is not a claim of OS-wide absence or future
non-use, and it is not executable-identity authentication.
Deliberate same-user entry swapping inside an already validated cache or
worktree during removal is an accepted residual outside this accident-prevention
threat model because `unlink`/`rmdir` do not follow links outside the target; all
nested-mount, cross-device, non-owned, protected-descendant, symlink, identity,
pathname, action-time, and other accidental or ambiguous drift refusals remain
mandatory.

## Eligibility gates (literal commands)

Every gate below is re-derived at action time, before the current-use scan and
again immediately before each action. Each command is the literal check; a
non-zero exit refuses. `$CANDIDATE` is the literal cache or worktree path;
`$CONTAINING` is the containing registered worktree (equal to `$CANDIDATE` for a
whole-worktree candidate), and for a cache candidate it must be the actual
registered root and is supplied explicitly. Worktree-level gates
(`non-primary`, `registration`, `clean`, `durable-preservation-and-pr`) inspect
`$CONTAINING`; per-path gates (`workspace-scope`, `ownership`,
`filesystem-ownership`, `protected-task`, `not-symlink`,
`same-filesystem`, `retention-age`, `cache-immediate-parent-manifest`,
`current-use-scan`) inspect `$CANDIDATE`. `$PRIMARY` is the owning primary
checkout, `$WORKSPACE` is your own agent workspace, `$AGE_SECONDS` is `86400` for
a cache or `604800` for a whole worktree, `$AGENT` is your own agent name, and
`$TASK_JSON` is the authoritative `happyranch recall` record for the owning
task. The commands are POSIX/Linux/macOS portable and use `python3` for
containment, identity, device, age and JSON so they never depend on a
platform-specific `stat`.

<!-- eligibility-commands:begin -->
```bash
# gate workspace-scope
python3 -c 'import os,sys
c,k,p,w=map(os.path.abspath,sys.argv[1:5]); t=sys.argv[5]; repo_parent=os.path.join(w,"repos")
ok=(all(x==os.path.realpath(x) for x in (c,k,p,w)) and os.path.dirname(p)==repo_parent and os.path.basename(p) not in ("",".") and k==os.path.join(p,".claude","worktrees",t) and (c==k or (os.path.basename(c) in ("node_modules",".venv") and os.path.dirname(c)==k)))
raise SystemExit(0 if ok else 1)' "$CANDIDATE" "$CONTAINING" "$PRIMARY" "$WORKSPACE" "$TASK"
# gate canonical-shape
python3 -c 'import os,sys; c=os.path.abspath(sys.argv[1]); w=os.path.abspath(sys.argv[2]); cr=os.path.realpath(c); wr=os.path.realpath(w); cache=os.path.basename(c) in ("node_modules",".venv"); immediate=os.path.dirname(c)==w; sys.exit(0 if c==cr and w==wr and (c==w or (cache and immediate and c!=w)) else 1)' "$CANDIDATE" "$CONTAINING"
# gate non-primary
python3 -c 'import os,sys; sys.exit(0 if os.path.realpath(sys.argv[1])!=os.path.realpath(sys.argv[2]) else 1)' "$CONTAINING" "$PRIMARY"
# gate registration
python3 -c 'import os,subprocess,sys
primary,want,task=sys.argv[1:]
p=subprocess.run(["git","-C",primary,"worktree","list","--porcelain"],capture_output=True,text=True)
if p.returncode: raise SystemExit(1)
blocks=[]
for raw in p.stdout.strip().split("\n\n"):
 d={}
 for line in raw.splitlines():
  key,_,value=line.partition(" "); d.setdefault(key,[]).append(value)
 blocks.append(d)
matches=[d for d in blocks if len(d.get("worktree",[]))==1 and os.path.realpath(d["worktree"][0])==want]
if len(matches)!=1 or matches[0].get("branch")!=["refs/heads/task/"+task]: raise SystemExit(1)
def common(path):
 q=subprocess.run(["git","-C",path,"rev-parse","--git-common-dir"],capture_output=True,text=True)
 if q.returncode or len(q.stdout.splitlines())!=1: raise SystemExit(1)
 value=q.stdout.strip(); return os.path.realpath(value if os.path.isabs(value) else os.path.join(path,value))
raise SystemExit(0 if common(primary)==common(want) else 1)' "$PRIMARY" "$CONTAINING" "$TASK"
# gate ownership
python3 -c 'import json,os,sys; d=json.load(open(os.environ["TASK_JSON"])); sys.exit(0 if d.get("assigned_agent")==sys.argv[1] else 1)' "$AGENT"
# gate filesystem-ownership
python3 -c 'import os,sys; uid=os.getuid(); sys.exit(0 if os.stat(sys.argv[1],follow_symlinks=False).st_uid==uid and os.stat(sys.argv[2],follow_symlinks=False).st_uid==uid else 1)' "$CANDIDATE" "$CONTAINING"
# gate protected-task
python3 -c 'import json,os,sys; d=json.load(open(os.environ["TASK_JSON"])); text=" ".join(str(d.get(k) or "") for k in ("output_summary","note")); sys.exit(1 if "worktree-deferred:" in text else 0)'
# gate not-symlink
python3 -c 'import os,pathlib,sys; p=pathlib.Path(os.path.abspath(sys.argv[1])); w=os.path.abspath(sys.argv[2]).rstrip("/"); anc=[str(p)]+[str(p.parents[i]) for i in range(len(p.parents))]; ins=[a for a in anc if a==w or a.startswith(w+"/")]; sys.exit(0 if all(not os.path.islink(a) for a in ins) else 1)' "$CANDIDATE" "$WORKSPACE"
# gate same-filesystem
python3 -c 'import os,sys; sys.exit(0 if os.stat(sys.argv[1]).st_dev==os.stat(sys.argv[2]).st_dev else 1)' "$CANDIDATE" "$PRIMARY"
# gate clean
python3 -c 'import subprocess,sys; p=subprocess.run(["git","-C",sys.argv[1],"status","--porcelain"],capture_output=True,text=True); sys.exit(1 if p.returncode or p.stdout else 0)' "$CONTAINING"
# gate durable-preservation-and-pr
_wc_git_preserved
# gate retention-age
python3 -c 'import datetime,json,os,sys; d=json.load(open(os.environ["TASK_JSON"])); ca=d.get("completed_at"); term=("completed","failed","cancelled","superseded"); sys.exit(1) if d.get("status") not in term or not ca else None; t=datetime.datetime.fromisoformat(str(ca).replace("Z","+00:00")); t=(t if t.tzinfo else t.replace(tzinfo=datetime.timezone.utc)); age=(datetime.datetime.now(datetime.timezone.utc)-t).total_seconds(); sys.exit(0 if age>=float(sys.argv[1]) else 1)' "$AGE_SECONDS"
# gate cache-immediate-parent-manifest
test -f "$(dirname "$CANDIDATE")/package-lock.json" || test -f "$(dirname "$CANDIDATE")/pnpm-lock.yaml" || test -f "$(dirname "$CANDIDATE")/yarn.lock" || test -f "$(dirname "$CANDIDATE")/uv.lock" || test -f "$(dirname "$CANDIDATE")/poetry.lock" || test -f "$(dirname "$CANDIDATE")/requirements.txt"
# gate current-use-scan
_wc_scan_job
# gate recursive-boundary
_wc_snapshot_tree
```
<!-- eligibility-commands:end -->

`cache-immediate-parent-manifest` applies only to a `node_modules`/`.venv`
cache; skip it for a whole worktree. `clean` applies only to a whole worktree;
every other gate applies to both. A
non-exempt unreadable same-user process, a missing/ambiguous containing
worktree, or any incomplete listing makes the scan `unknown` -> skip; a positive
non-exempt use makes it `blocked` -> skip; only `clear_observation` with every
gate exit 0 permits the non-force action.

## Delivered procedure (executable control flow)

This is the exact sequence to execute: authoritative joins first, then every
gate, then — only if all of them passed — the literal non-force action. A
refusal performs **no** mutation. Source this block and call
`run_cleanup_candidate <candidate> <containing>`; it prints a JSON receipt and
returns `0` only when the literal action ran, `2` on any refusal.

<!-- procedure-commands:begin -->
```bash
_wc_gate_block() {
  awk '/<!-- eligibility-commands:begin -->/{f=1;next}
       /<!-- eligibility-commands:end -->/{f=0}
       f' "$SKILL/SKILL.md" | grep -v '^[`][`][`]'
}

_wc_refuse() { printf '{"decision":"%s","reason":"%s"}\n' "${2:-refused}" "$1"; return 2; }

_wc_is_cache() {
  case "$(basename "$CANDIDATE")" in node_modules|.venv) return 0;; *) return 1;; esac
}

_wc_snapshot_tree() {
  local snapshot="$WC_TMP/tree-boundary.json"
  python3 - "$CANDIDATE" "$CONTAINING" "$PRIMARY" "$WORKSPACE" "$snapshot" <<'PY'
import json, os, re, stat, subprocess, sys, tempfile

candidate, containing, primary, workspace, destination = map(os.path.abspath, sys.argv[1:])
uid = os.getuid()
cap = 200000

def mountpoints():
    result = set()
    try:
        with open("/proc/self/mountinfo", encoding="utf-8") as handle:
            for line in handle:
                fields = line.split()
                if len(fields) < 5:
                    raise RuntimeError("malformed_mountinfo")
                value = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), fields[4])
                result.add(os.path.abspath(value))
        return result
    except FileNotFoundError:
        proc = subprocess.run(["mount"], capture_output=True, text=True, timeout=5)
        if proc.returncode:
            raise RuntimeError("mount_table_unavailable")
        for line in proc.stdout.splitlines():
            match = re.search(r" on (.+?) \\(", line)
            if not match:
                raise RuntimeError("malformed_mount_table")
            result.add(os.path.abspath(match.group(1)))
        return result

def identity(path):
    value = os.lstat(path)
    return [value.st_dev, value.st_ino, value.st_mode, value.st_uid]

protected_paths = [
    workspace,
    os.path.join(workspace, "repos"),
    primary,
    os.path.join(primary, ".git"),
    os.path.join(workspace, "output"),
    os.path.join(workspace, ".happyranch"),
    os.path.join(workspace, ".agents"),
    os.path.join(workspace, ".claude"),
]
if containing != candidate:
    protected_paths.extend([containing, os.path.join(containing, ".git")])
for protected_path in protected_paths:
    if protected_path != candidate and protected_path.startswith(candidate.rstrip(os.sep) + os.sep):
        raise RuntimeError("protected_descendant")

def snapshot():
    mounts = mountpoints()
    root = os.lstat(candidate)
    if not stat.S_ISDIR(root.st_mode) or stat.S_ISLNK(root.st_mode) or root.st_uid != uid:
        raise RuntimeError("invalid_root")
    root_dev = root.st_dev
    rows = []
    pending = [candidate]
    while pending:
        path = pending.pop()
        value = os.lstat(path)
        if value.st_uid != uid:
            raise RuntimeError("non_owned_entry")
        if value.st_dev != root_dev:
            raise RuntimeError("cross_device_entry")
        if path != candidate and path in mounts:
            raise RuntimeError("nested_mount")
        rel = os.path.relpath(path, candidate)
        if rel == ".":
            rel = ""
        if stat.S_ISLNK(value.st_mode):
            resolved = os.path.realpath(path)
            if resolved != candidate and not resolved.startswith(candidate.rstrip(os.sep) + os.sep):
                raise RuntimeError("external_or_protected_symlink")
        rows.append([
            rel, value.st_dev, value.st_ino, value.st_mode, value.st_uid,
            value.st_size, getattr(value, "st_blocks", 0),
        ])
        if len(rows) > cap:
            raise RuntimeError("entry_cap")
        if stat.S_ISDIR(value.st_mode) and not stat.S_ISLNK(value.st_mode):
            try:
                with os.scandir(path) as entries:
                    children = [entry.path for entry in entries]
            except OSError as exc:
                raise RuntimeError("unreadable_entry") from exc
            pending.extend(sorted(children, reverse=True))
    protected = {
        path: (identity(path) if os.path.lexists(path) else None)
        for path in protected_paths
    }
    return {"tree": sorted(rows), "protected": protected}

first = snapshot()
second = snapshot()
if first != second:
    raise SystemExit(2)
if os.path.exists(destination):
    with open(destination, encoding="utf-8") as handle:
        if json.load(handle) != first:
            raise SystemExit(3)
else:
    parent = os.path.dirname(destination)
    fd, temporary = tempfile.mkstemp(prefix="tree-boundary-", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(first, handle, sort_keys=True)
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
PY
}

_wc_validate_isolated_cache() {
  python3 - "$CANDIDATE" "$WC_ISOLATED_CANDIDATE" "$WC_ISOLATION_DIR" \
    "$CONTAINING" "$WC_TMP/tree-boundary.json" <<'PY'
import json, os, re, stat, subprocess, sys

candidate, isolated, isolation_dir, containing, snapshot_path = map(
    os.path.abspath, sys.argv[1:]
)
uid = os.getuid()
cap = 200000

def mountpoints():
    result = set()
    try:
        with open("/proc/self/mountinfo", encoding="utf-8") as handle:
            for line in handle:
                fields = line.split()
                if len(fields) < 5:
                    raise RuntimeError("malformed_mountinfo")
                value = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), fields[4])
                result.add(os.path.abspath(value))
        return result
    except FileNotFoundError:
        proc = subprocess.run(["mount"], capture_output=True, text=True, timeout=5)
        if proc.returncode:
            raise RuntimeError("mount_table_unavailable")
        for line in proc.stdout.splitlines():
            match = re.search(r" on (.+?) \\(", line)
            if not match:
                raise RuntimeError("malformed_mount_table")
            result.add(os.path.abspath(match.group(1)))
        return result

def identity(path):
    value = os.lstat(path)
    return [value.st_dev, value.st_ino, value.st_mode, value.st_uid]

def protected_snapshot(paths):
    return {
        path: (identity(path) if os.path.lexists(path) else None)
        for path in paths
    }

def tree_snapshot(root_path):
    mounts = mountpoints()
    root = os.lstat(root_path)
    if not stat.S_ISDIR(root.st_mode) or stat.S_ISLNK(root.st_mode) or root.st_uid != uid:
        raise RuntimeError("invalid_isolated_root")
    root_dev = root.st_dev
    rows = []
    pending = [root_path]
    while pending:
        path = pending.pop()
        value = os.lstat(path)
        if value.st_uid != uid:
            raise RuntimeError("non_owned_entry")
        if value.st_dev != root_dev:
            raise RuntimeError("cross_device_entry")
        if path != root_path and path in mounts:
            raise RuntimeError("nested_mount")
        rel = os.path.relpath(path, root_path)
        if rel == ".":
            rel = ""
        if stat.S_ISLNK(value.st_mode):
            resolved = os.path.realpath(path)
            if resolved != root_path and not resolved.startswith(root_path.rstrip(os.sep) + os.sep):
                raise RuntimeError("external_or_protected_symlink")
        rows.append([
            rel, value.st_dev, value.st_ino, value.st_mode, value.st_uid,
            value.st_size, getattr(value, "st_blocks", 0),
        ])
        if len(rows) > cap:
            raise RuntimeError("entry_cap")
        if stat.S_ISDIR(value.st_mode) and not stat.S_ISLNK(value.st_mode):
            try:
                with os.scandir(path) as entries:
                    children = [entry.path for entry in entries]
            except OSError as exc:
                raise RuntimeError("unreadable_entry") from exc
            pending.extend(sorted(children, reverse=True))
    return sorted(rows)

with open(snapshot_path, encoding="utf-8") as handle:
    expected = json.load(handle)
if set(expected) != {"tree", "protected"}:
    raise SystemExit(2)
if os.path.dirname(isolation_dir) != containing:
    raise SystemExit(3)
if os.path.dirname(isolated) != isolation_dir:
    raise SystemExit(3)
if not os.path.basename(isolation_dir).startswith(".workspace-cleanup-isolate."):
    raise SystemExit(3)
if (os.path.basename(candidate) not in ("node_modules", ".venv")
        or os.path.basename(isolated) != os.path.basename(candidate)):
    raise SystemExit(3)
if os.path.lexists(candidate):
    raise SystemExit(4)
isolation_identity = os.lstat(isolation_dir)
if (not stat.S_ISDIR(isolation_identity.st_mode)
        or stat.S_ISLNK(isolation_identity.st_mode)
        or isolation_identity.st_uid != uid
        or stat.S_IMODE(isolation_identity.st_mode) != 0o700):
    raise SystemExit(5)
root_rows = [row for row in expected["tree"] if row[0] == ""]
if len(root_rows) != 1 or isolation_identity.st_dev != root_rows[0][1]:
    raise SystemExit(5)
expected_protected = expected["protected"]
if protected_snapshot(expected_protected) != expected_protected:
    raise SystemExit(6)
first = tree_snapshot(isolated)
if os.path.lexists(candidate):
    raise SystemExit(7)
second = tree_snapshot(isolated)
if first != second or first != expected["tree"]:
    raise SystemExit(8)
if protected_snapshot(expected_protected) != expected_protected:
    raise SystemExit(9)
if os.path.lexists(candidate):
    raise SystemExit(10)
PY
}

_wc_delete_isolated_cache() {
  WC_DELETE_STARTED="$WC_TMP/delete-isolated-started"
  export WC_DELETE_STARTED
  python3 - --workspace-cleanup-delete-isolated-v1 \
    "$CANDIDATE" "$WC_ISOLATED_CANDIDATE" "$WC_ISOLATION_DIR" \
    "$CONTAINING" "$WC_TMP/tree-boundary.json" "$WC_DELETE_STARTED" <<'PY'
import json, os, re, stat, subprocess, sys

marker, candidate, isolated, isolation_dir, containing, snapshot_path, started = sys.argv[1:]
if marker != "--workspace-cleanup-delete-isolated-v1":
    raise SystemExit(2)
candidate, isolated, isolation_dir, containing, snapshot_path, started = map(
    os.path.abspath,
    (candidate, isolated, isolation_dir, containing, snapshot_path, started),
)
uid = os.getuid()
cap = 200000

# Refuse before mutation on a platform that cannot keep traversal rooted in
# already opened, no-follow directory descriptors.
required = (os.open, os.stat, os.unlink, os.rmdir)
if (not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW")
        or os.listdir not in os.supports_fd
        or any(operation not in os.supports_dir_fd for operation in required)
        or os.stat not in os.supports_follow_symlinks):
    raise SystemExit(3)

directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
if hasattr(os, "O_CLOEXEC"):
    directory_flags |= os.O_CLOEXEC

def mountpoints():
    result = set()
    try:
        with open("/proc/self/mountinfo", encoding="utf-8") as handle:
            for line in handle:
                fields = line.split()
                if len(fields) < 5:
                    raise RuntimeError("malformed_mountinfo")
                value = re.sub(
                    r"\\([0-7]{3})",
                    lambda match: chr(int(match.group(1), 8)),
                    fields[4],
                )
                result.add(os.path.abspath(value))
        return result
    except FileNotFoundError:
        process = subprocess.run(
            ["mount"], capture_output=True, text=True, timeout=5,
        )
        if process.returncode:
            raise RuntimeError("mount_table_unavailable")
        for line in process.stdout.splitlines():
            match = re.search(r" on (.+?) \\(", line)
            if not match:
                raise RuntimeError("malformed_mount_table")
            result.add(os.path.abspath(match.group(1)))
        return result

def identity(value):
    return [value.st_dev, value.st_ino, value.st_mode, value.st_uid]

def row(relative, value):
    return [
        relative, value.st_dev, value.st_ino, value.st_mode, value.st_uid,
        value.st_size, getattr(value, "st_blocks", 0),
    ]

def protected_snapshot(paths):
    return {
        path: (identity(os.lstat(path)) if os.path.lexists(path) else None)
        for path in paths
    }

def opened_directory(parent_fd, name):
    before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    descriptor = os.open(name, directory_flags, dir_fd=parent_fd)
    after = os.fstat(descriptor)
    if identity(before) != identity(after):
        os.close(descriptor)
        raise RuntimeError("directory_identity_changed")
    return descriptor, after

def descriptor_tree(root_fd):
    mounts = mountpoints()
    root = os.fstat(root_fd)
    if (not stat.S_ISDIR(root.st_mode) or stat.S_ISLNK(root.st_mode)
            or root.st_uid != uid):
        raise RuntimeError("invalid_isolated_root")
    root_dev = root.st_dev
    rows = [row("", root)]

    def walk(directory_fd, relative):
        try:
            names = sorted(os.listdir(directory_fd))
        except OSError as exc:
            raise RuntimeError("unreadable_entry") from exc
        for name in names:
            child_rel = os.path.join(relative, name) if relative else name
            child_path = os.path.join(isolated, child_rel)
            value = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if value.st_uid != uid:
                raise RuntimeError("non_owned_entry")
            if value.st_dev != root_dev:
                raise RuntimeError("cross_device_entry")
            if child_path in mounts:
                raise RuntimeError("nested_mount")
            rows.append(row(child_rel, value))
            if len(rows) > cap:
                raise RuntimeError("entry_cap")
            if stat.S_ISDIR(value.st_mode) and not stat.S_ISLNK(value.st_mode):
                child_fd, opened = opened_directory(directory_fd, name)
                try:
                    if row(child_rel, opened) != row(child_rel, value):
                        raise RuntimeError("directory_identity_changed")
                    walk(child_fd, child_rel)
                finally:
                    os.close(child_fd)

    walk(root_fd, "")
    return sorted(rows)

def remove_tree(root_fd, expected_rows):
    expected = {item[0]: item for item in expected_rows}

    def direct_children(relative):
        prefix = relative + os.sep if relative else ""
        return {
            path[len(prefix):]
            for path in expected
            if path.startswith(prefix)
            and path != relative
            and os.sep not in path[len(prefix):]
        }

    def remove_children(directory_fd, relative):
        names = set(os.listdir(directory_fd))
        if names != direct_children(relative):
            raise RuntimeError("entry_set_changed")
        for name in sorted(names):
            child_rel = os.path.join(relative, name) if relative else name
            before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if row(child_rel, before) != expected[child_rel]:
                raise RuntimeError("entry_identity_changed")
            if stat.S_ISDIR(before.st_mode) and not stat.S_ISLNK(before.st_mode):
                child_fd, opened = opened_directory(directory_fd, name)
                try:
                    if row(child_rel, opened) != expected[child_rel]:
                        raise RuntimeError("entry_identity_changed")
                    remove_children(child_fd, child_rel)
                    current = os.stat(
                        name, dir_fd=directory_fd, follow_symlinks=False,
                    )
                    if identity(current) != identity(opened):
                        raise RuntimeError("entry_identity_changed")
                    os.rmdir(name, dir_fd=directory_fd)
                finally:
                    os.close(child_fd)
            else:
                os.unlink(name, dir_fd=directory_fd)
        if os.listdir(directory_fd):
            raise RuntimeError("entry_residual")

    remove_children(root_fd, "")

with open(snapshot_path, encoding="utf-8") as handle:
    expected = json.load(handle)
if set(expected) != {"tree", "protected"}:
    raise SystemExit(4)
if (os.path.dirname(isolation_dir) != containing
        or os.path.dirname(isolated) != isolation_dir
        or not os.path.basename(isolation_dir).startswith(
            ".workspace-cleanup-isolate."
        )
        or os.path.basename(candidate) not in ("node_modules", ".venv")
        or os.path.basename(isolated) != os.path.basename(candidate)
        or os.path.lexists(candidate)):
    raise SystemExit(5)

containing_fd = isolation_fd = isolated_fd = None
try:
    containing_fd = os.open(containing, directory_flags)
    if identity(os.fstat(containing_fd)) != identity(os.lstat(containing)):
        raise RuntimeError("containing_identity_changed")
    isolation_fd, isolation_value = opened_directory(
        containing_fd, os.path.basename(isolation_dir),
    )
    if (isolation_value.st_uid != uid
            or stat.S_IMODE(isolation_value.st_mode) != 0o700):
        raise RuntimeError("invalid_isolation_directory")
    isolated_fd, isolated_value = opened_directory(
        isolation_fd, os.path.basename(isolated),
    )
    root_rows = [item for item in expected["tree"] if item[0] == ""]
    if len(root_rows) != 1 or row("", isolated_value) != root_rows[0]:
        raise RuntimeError("isolated_root_changed")
    if isolation_value.st_dev != isolated_value.st_dev:
        raise RuntimeError("isolation_device_changed")
    if protected_snapshot(expected["protected"]) != expected["protected"]:
        raise RuntimeError("protected_identity_changed")
    first = descriptor_tree(isolated_fd)
    second = descriptor_tree(isolated_fd)
    if first != second or first != expected["tree"]:
        raise RuntimeError("isolated_tree_changed")
    if (os.path.lexists(candidate)
            or identity(os.lstat(isolation_dir)) != identity(isolation_value)
            or identity(os.lstat(isolated)) != identity(isolated_value)
            or protected_snapshot(expected["protected"]) != expected["protected"]):
        raise RuntimeError("action_boundary_changed")

    started_fd = os.open(
        started, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600,
    )
    try:
        os.write(started_fd, b"descriptor-rooted-delete-started\n")
    finally:
        os.close(started_fd)

    remove_tree(isolated_fd, expected["tree"])
    current = os.stat(
        os.path.basename(isolated),
        dir_fd=isolation_fd,
        follow_symlinks=False,
    )
    if identity(current) != identity(isolated_value):
        raise RuntimeError("isolated_root_changed")
    os.rmdir(os.path.basename(isolated), dir_fd=isolation_fd)
    if os.path.lexists(candidate):
        raise RuntimeError("candidate_recreated")
    if protected_snapshot(expected["protected"]) != expected["protected"]:
        raise RuntimeError("protected_identity_changed")
    current_isolation = os.stat(
        os.path.basename(isolation_dir),
        dir_fd=containing_fd,
        follow_symlinks=False,
    )
    if identity(current_isolation) != identity(isolation_value):
        raise RuntimeError("isolation_identity_changed")
    if os.listdir(isolation_fd):
        raise RuntimeError("isolation_residual")
    os.rmdir(os.path.basename(isolation_dir), dir_fd=containing_fd)
except (OSError, RuntimeError, ValueError, KeyError, TypeError):
    raise SystemExit(6)
finally:
    for descriptor in (isolated_fd, isolation_fd, containing_fd):
        if descriptor is not None:
            os.close(descriptor)
PY
}

_wc_restore_isolated_cache() {
  [ -n "${WC_ISOLATED_CANDIDATE:-}" ] || return 1
  [ -n "${WC_ISOLATION_DIR:-}" ] || return 1
  if [ -e "$WC_ISOLATED_CANDIDATE" ] || [ -L "$WC_ISOLATED_CANDIDATE" ]; then
    if [ -e "$CANDIDATE" ] || [ -L "$CANDIDATE" ]; then
      return 1
    fi
    mv -- "$WC_ISOLATED_CANDIDATE" "$CANDIDATE" || return 1
  fi
  if [ -e "$WC_ISOLATION_DIR" ] || [ -L "$WC_ISOLATION_DIR" ]; then
    rmdir -- "$WC_ISOLATION_DIR" || return 1
  fi
  return 0
}

_wc_isolate_cache() {
  if ! WC_ISOLATION_DIR="$(mktemp -d "$CONTAINING/.workspace-cleanup-isolate.XXXXXX")"; then
    return 1
  fi
  WC_ISOLATED_CANDIDATE="$WC_ISOLATION_DIR/$(basename "$CANDIDATE")"
  export WC_ISOLATION_DIR WC_ISOLATED_CANDIDATE
  if ! mv -- "$CANDIDATE" "$WC_ISOLATED_CANDIDATE"; then
    rmdir -- "$WC_ISOLATION_DIR" 2>/dev/null || :
    return 1
  fi
  _wc_validate_isolated_cache
}

_wc_verify_post_action() {
  python3 - "$CANDIDATE" "$WC_TMP/tree-boundary.json" \
    "${WC_ISOLATED_CANDIDATE:-}" "${WC_ISOLATION_DIR:-}" <<'PY'
import json, os, sys

candidate, snapshot_path, isolated, isolation_dir = sys.argv[1:]
if os.path.lexists(candidate):
    raise SystemExit(2)
for path in (isolated, isolation_dir):
    if path and os.path.lexists(path):
        raise SystemExit(2)
with open(snapshot_path, encoding="utf-8") as handle:
    protected = json.load(handle)["protected"]
for path, expected in protected.items():
    if expected is None:
        if os.path.lexists(path):
            raise SystemExit(3)
        continue
    try:
        value = os.lstat(path)
    except OSError:
        raise SystemExit(3)
    actual = [value.st_dev, value.st_ino, value.st_mode, value.st_uid]
    if actual != expected:
        raise SystemExit(4)
PY
}

_wc_git_preserved() {
  local head branch remote_url repo_slug repo_owner repo_name pr_query
  local pr_first pr_second remote_rows remote_rc
  local durable_ref=0 remote_match=0 merged_match
  head="$(git -C "$CONTAINING" rev-parse HEAD 2>/dev/null)" || return 1
  branch="$(git -C "$CONTAINING" rev-parse --abbrev-ref HEAD 2>/dev/null)" || return 1
  [ "$branch" = "task/$TASK" ] || return 1
  remote_url="$(git -C "$PRIMARY" remote get-url origin 2>/dev/null)" || return 1
  repo_slug="$(python3 -c 'import re,sys
u=sys.argv[1].strip(); m=re.fullmatch(r"(?:https://github[.]com/|git@github[.]com:)([^/]+/[^/]+?)(?:[.]git)?",u); print(m.group(1) if m else "")' "$remote_url")"
  [ -n "$repo_slug" ] || return 1
  repo_owner="${repo_slug%/*}"; repo_name="${repo_slug#*/}"
  pr_query='query($owner:String!,$name:String!,$headRefName:String!,$endCursor:String){repository(owner:$owner,name:$name){pullRequests(first:100,after:$endCursor,headRefName:$headRefName,orderBy:{field:CREATED_AT,direction:ASC}){totalCount nodes{number state mergedAt headRefName headRefOid} pageInfo{hasNextPage endCursor}}}}'
  pr_first="$WC_TMP/pr-pages-first.json"
  pr_second="$WC_TMP/pr-pages-second.json"
  gh api graphql --paginate --slurp -f query="$pr_query" \
    -F owner="$repo_owner" -F name="$repo_name" -F headRefName="$branch" \
    > "$pr_first" 2>/dev/null || return 1
  gh api graphql --paginate --slurp -f query="$pr_query" \
    -F owner="$repo_owner" -F name="$repo_name" -F headRefName="$branch" \
    > "$pr_second" 2>/dev/null || return 1
  merged_match="$(python3 -c 'import json,sys
def parse(path,branch,head):
 pages=json.load(open(path))
 if not isinstance(pages,list) or not pages: raise SystemExit(2)
 rows=[]; seen=set(); totals=set(); cursors=set()
 for index,page in enumerate(pages):
  if not isinstance(page,dict) or set(page)!={"data"}: raise SystemExit(2)
  data=page["data"]
  if not isinstance(data,dict) or set(data)!={"repository"} or not isinstance(data["repository"],dict): raise SystemExit(2)
  repo=data["repository"]
  if set(repo)!={"pullRequests"} or not isinstance(repo["pullRequests"],dict): raise SystemExit(2)
  prs=repo["pullRequests"]
  if set(prs)!={"totalCount","nodes","pageInfo"}: raise SystemExit(2)
  total=prs["totalCount"]; nodes=prs["nodes"]; info=prs["pageInfo"]
  if isinstance(total,bool) or not isinstance(total,int) or total<0 or not isinstance(nodes,list) or len(nodes)>100: raise SystemExit(2)
  if not isinstance(info,dict) or set(info)!={"hasNextPage","endCursor"} or not isinstance(info["hasNextPage"],bool): raise SystemExit(2)
  cursor=info["endCursor"]
  if index<len(pages)-1:
   if info["hasNextPage"] is not True or not isinstance(cursor,str) or not cursor or cursor in cursors: raise SystemExit(2)
   cursors.add(cursor)
  elif info["hasNextPage"] is not False: raise SystemExit(2)
  totals.add(total)
  for row in nodes:
   if not isinstance(row,dict) or set(row)!={"number","state","mergedAt","headRefName","headRefOid"}: raise SystemExit(2)
   number=row["number"]
   if isinstance(number,bool) or not isinstance(number,int) or number<=0 or number in seen: raise SystemExit(2)
   seen.add(number)
   if row["headRefName"]!=branch or row["headRefOid"]!=head or row["state"] not in ("OPEN","CLOSED","MERGED"): raise SystemExit(2)
   if row["state"]!="MERGED" or not isinstance(row["mergedAt"],str) or not row["mergedAt"]: raise SystemExit(3)
   rows.append(row)
 if len(totals)!=1 or len(rows)!=next(iter(totals)): raise SystemExit(2)
 return rows
a=parse(sys.argv[1],sys.argv[3],sys.argv[4]); b=parse(sys.argv[2],sys.argv[3],sys.argv[4])
if a!=b: raise SystemExit(4)
if len(a)>1: raise SystemExit(5)
print("1" if len(a)==1 else "0")' "$pr_first" "$pr_second" "$branch" "$head")" || return 1
  if git -C "$CONTAINING" merge-base --is-ancestor HEAD origin/main 2>/dev/null; then
    durable_ref=1
  fi
  remote_rows="$WC_TMP/remote-branch.txt"
  git -C "$PRIMARY" ls-remote --exit-code origin "refs/heads/$branch" > "$remote_rows" 2>/dev/null
  remote_rc=$?
  if [ "$remote_rc" -eq 0 ]; then
    if python3 -c 'import re,sys
rows=[x.split() for x in open(sys.argv[1]) if x.strip()]
ok=len(rows)==1 and re.fullmatch(r"[0-9a-f]{40}",rows[0][0]) and rows[0][0]==sys.argv[2] and rows[0][1]=="refs/heads/"+sys.argv[3]
raise SystemExit(0 if ok else 1)' "$remote_rows" "$head" "$branch"; then
      remote_match=1
    else
      return 1
    fi
  elif [ "$remote_rc" -ne 2 ]; then
    return 1
  fi
  [ "$durable_ref" -eq 1 ] || [ "$remote_match" -eq 1 ] || [ "$merged_match" = "1" ]
}

_wc_scan_job() {
  local nonce title rationale scan_script payload submit_json wait_json
  local job_id show_json output_json started_epoch
  [ -n "${ACTING_TASK:-}" ] && [ -n "${SESSION_ID:-}" ] || return 1
  nonce="wc-${ACTING_TASK}-$$-${RANDOM:-0}"
  title="Workspace cleanup path-use scan $nonce"
  rationale="Host-visible read-only workspace cleanup scan for task $ACTING_TASK session $SESSION_ID nonce $nonce"
  scan_script="$(python3 -c 'import shlex,sys; print("exec "+shlex.join(sys.argv[1:]))' \
    python3 "$SKILL/scripts/check_path_use.py" --target "$CANDIDATE" \
    --containing-worktree "$CONTAINING" --json)" || return 1
  payload="$WC_TMP/scan-job-$nonce.json"
  export title rationale scan_script nonce
  if ! python3 -c 'import json,os,sys
json.dump({"task_id":os.environ["ACTING_TASK"],"session_id":os.environ["SESSION_ID"],"title":os.environ["title"],"rationale":os.environ["rationale"],"script":os.environ["scan_script"]+"\n","interpreter":"bash","review_required":False,"persistent":False,"max_runtime_seconds":20},open(sys.argv[1],"w"),sort_keys=True)' "$payload"; then
    return 1
  fi
  started_epoch="$(date -u +%s)" || return 1
  submit_json="$WC_TMP/scan-submit-$nonce.json"
  if ! happyranch jobs submit --org "$ORG" --from-file "$payload" --json > "$submit_json" 2>/dev/null; then
    return 1
  fi
  job_id="$(python3 -c 'import json,os,re,sys
d=json.load(open(sys.argv[1])); required={"id","status","created_at","started_at","cwd_resolved","timeout_seconds","events_url","authentication"}
if not isinstance(d,dict) or set(d)!=required or d.get("status")!="running": raise SystemExit(1)
auth=d.get("authentication")
if auth!={"task_id":os.environ["ACTING_TASK"],"session_id":os.environ["SESSION_ID"]}: raise SystemExit(1)
job=d.get("id")
if not isinstance(job,str) or re.fullmatch(r"JOB-[0-9]+",job) is None: raise SystemExit(1)
if d.get("cwd_resolved")!=os.path.realpath(os.environ["WORKSPACE"]): raise SystemExit(1)
print(job)' "$submit_json")"
  [ -n "$job_id" ] || return 1
  wait_json="$WC_TMP/scan-wait-$nonce.json"
  if ! happyranch jobs wait "$job_id" --timeout-seconds 30 --task-id "$ACTING_TASK" \
        --session-id "$SESSION_ID" --org "$ORG" > "$wait_json" 2>/dev/null; then
    return 1
  fi
  if ! python3 -c 'import json,sys
d=json.load(open(sys.argv[1])); raise SystemExit(0 if d=={"status":"completed","timed_out":False} else 1)' "$wait_json"; then
    return 1
  fi
  show_json="$WC_TMP/scan-show-$nonce.json"
  output_json="$WC_TMP/scan-output-$nonce.json"
  happyranch jobs show "$job_id" --org "$ORG" --json \
    --task-id "$ACTING_TASK" --session-id "$SESSION_ID" > "$show_json" 2>/dev/null || return 1
  happyranch jobs output "$job_id" --stream both --max-bytes 1048576 \
    --org "$ORG" --json --task-id "$ACTING_TASK" --session-id "$SESSION_ID" \
    > "$output_json" 2>/dev/null || return 1
  export job_id started_epoch
  if ! cmp -s "$show_json" "$output_json" || ! python3 - "$show_json" <<'PY'
import datetime, json, os, re, sys, time

receipt = json.load(open(sys.argv[1]))
if not isinstance(receipt, dict) or set(receipt) != {"authentication", "job", "output"}:
    raise SystemExit(1)
if receipt["authentication"] != {
    "task_id": os.environ["ACTING_TASK"],
    "session_id": os.environ["SESSION_ID"],
}:
    raise SystemExit(1)
job = receipt["job"]
job_keys = {
    "id", "task_id", "agent_name", "title", "rationale", "script_text",
    "interpreter", "cwd_hint", "cwd_resolved", "status", "exit_code",
    "reason", "duration_ms", "created_at", "started_at", "finished_at",
}
if not isinstance(job, dict) or set(job) != job_keys:
    raise SystemExit(1)
if job != job | {
    "id": os.environ["job_id"],
    "task_id": os.environ["ACTING_TASK"],
    "agent_name": os.environ["AGENT"],
    "title": os.environ["title"],
    "rationale": os.environ["rationale"],
    "script_text": os.environ["scan_script"] + "\n",
    "interpreter": "bash",
    "cwd_hint": None,
    "cwd_resolved": os.path.realpath(os.environ["WORKSPACE"]),
    "status": "completed",
    "exit_code": 0,
    "reason": None,
}:
    raise SystemExit(1)
if isinstance(job["duration_ms"], bool) or not isinstance(job["duration_ms"], int) or job["duration_ms"] < 0:
    raise SystemExit(1)
def instant(value):
    if not isinstance(value, str):
        raise SystemExit(1)
    parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise SystemExit(1)
    return parsed.timestamp()
created, started, finished = map(instant, (job["created_at"], job["started_at"], job["finished_at"]))
submitted = float(os.environ["started_epoch"])
observed = time.time()
if created < submitted - 2 or finished > observed + 2 or not created <= started <= finished:
    raise SystemExit(1)
output = receipt["output"]
output_keys = {"stdout", "stderr", "truncated_stdout", "truncated_stderr", "total_stdout_bytes", "total_stderr_bytes"}
if not isinstance(output, dict) or set(output) != output_keys:
    raise SystemExit(1)
if not isinstance(output["stdout"], str) or not isinstance(output["stderr"], str):
    raise SystemExit(1)
if output["truncated_stdout"] is not False or output["truncated_stderr"] is not False or output["stderr"] != "":
    raise SystemExit(1)
if output["total_stdout_bytes"] != len(output["stdout"].encode()) or output["total_stderr_bytes"] != 0:
    raise SystemExit(1)
scan = json.loads(output["stdout"])
if not isinstance(scan, dict) or set(scan) != {"state", "target", "hits", "reasons", "coverage", "exempt", "cycles"}:
    raise SystemExit(1)
if scan["state"] != "clear_observation" or scan["target"] != os.path.realpath(os.environ["CANDIDATE"]):
    raise SystemExit(1)
if scan["hits"] != [] or scan["reasons"] != [] or not isinstance(scan["exempt"], list) or not isinstance(scan["cycles"], list):
    raise SystemExit(1)
coverage = scan["coverage"]
coverage_keys = {
    "agent_uid", "self_pid", "target_dev_ino", "containing_worktree",
    "containing_worktree_dev_ino", "target_present", "containing_worktree_present",
    "total_pids", "same_user", "root", "other_user", "exempt", "scanned",
    "exited", "unreadable_same_user", "unreadable_unknown_uid",
    "identity_read_errors", "role_mismatch", "denied", "vanished", "errors",
    "truncated", "maps_truncated", "fd_truncated", "threads_truncated",
    "new_pids_after", "reused_pids", "mnt_ns_differs",
    "mnt_ns_path_unverified", "host_context", "enum_passes",
}
if not isinstance(coverage, dict) or set(coverage) != coverage_keys:
    raise SystemExit(1)
if isinstance(coverage["agent_uid"], bool) or not isinstance(coverage["agent_uid"], int):
    raise SystemExit(1)
if not isinstance(coverage["self_pid"], str) or not coverage["self_pid"].isdigit():
    raise SystemExit(1)
target_stat = os.stat(os.environ["CANDIDATE"])
if coverage["target_present"] is not True or coverage["target_dev_ino"] != [target_stat.st_dev, target_stat.st_ino]:
    raise SystemExit(1)
is_cache = os.path.basename(os.environ["CANDIDATE"]) in ("node_modules", ".venv")
if is_cache:
    containing_stat = os.stat(os.environ["CONTAINING"])
    if coverage["containing_worktree"] != os.path.realpath(os.environ["CONTAINING"]):
        raise SystemExit(1)
    if coverage["containing_worktree_present"] is not True or coverage["containing_worktree_dev_ino"] != [containing_stat.st_dev, containing_stat.st_ino]:
        raise SystemExit(1)
else:
    if coverage["containing_worktree"] is not None or coverage["containing_worktree_dev_ino"] is not None or coverage["containing_worktree_present"] is not False:
        raise SystemExit(1)
counters = coverage_keys - {"self_pid", "target_dev_ino", "containing_worktree", "containing_worktree_dev_ino", "target_present", "containing_worktree_present", "host_context"}
for key in counters:
    if isinstance(coverage[key], bool) or not isinstance(coverage[key], int) or coverage[key] < 0:
        raise SystemExit(1)
if coverage["enum_passes"] < 1:
    raise SystemExit(1)
for key in ("unreadable_same_user", "unreadable_unknown_uid", "identity_read_errors", "denied", "errors", "truncated", "maps_truncated", "fd_truncated", "threads_truncated", "reused_pids", "mnt_ns_path_unverified"):
    if coverage[key] != 0:
        raise SystemExit(1)
host = coverage["host_context"]
host_keys = {"pid1_comm", "pid_ns_agree", "mnt_agree", "proc_mounts", "stacked", "mountinfo", "pid1_ns_readable", "ok"}
if not isinstance(host, dict) or set(host) != host_keys or host["ok"] is not True or host["stacked"] is not False:
    raise SystemExit(1)
if host["pid1_comm"] not in ("systemd", "init") or not isinstance(host["proc_mounts"], int) or host["proc_mounts"] != 1:
    raise SystemExit(1)
for key in ("pid_ns_agree", "mnt_agree", "stacked", "pid1_ns_readable", "ok"):
    if not isinstance(host[key], bool):
        raise SystemExit(1)
if not isinstance(host["mountinfo"], str):
    raise SystemExit(1)
for row in scan["exempt"]:
    if not isinstance(row, dict) or set(row) != {"pid", "role", "basis", "exe", "comm", "unit", "gaps"}:
        raise SystemExit(1)
    if not isinstance(row["pid"], str) or not row["pid"].isdigit() or row["basis"] != "name_and_cgroup_role" or not isinstance(row["gaps"], list):
        raise SystemExit(1)
for row in scan["cycles"]:
    if not isinstance(row, dict) or set(row) != {"phase", "pass", "new"} or row["phase"] != "enumerate_pass":
        raise SystemExit(1)
    if any(isinstance(row[key], bool) or not isinstance(row[key], int) or row[key] < 0 for key in ("pass", "new")):
        raise SystemExit(1)
PY
  then
    _wc_refuse "scan_receipt_mismatch" >/dev/null
    return 1
  fi
  return 0
}

_wc_measure_path() {
  python3 -c 'import json,os,sys
p=sys.argv[1]; apparent=allocated=0
def add(q):
    global apparent,allocated
    st=os.lstat(q); apparent+=st.st_size; allocated+=getattr(st,"st_blocks",0)*512
add(p)
def failed(exc): raise exc
for root,dirs,files in os.walk(p,followlinks=False,onerror=failed):
    for name in dirs+files:
        q=os.path.join(root,name)
        try: add(q)
        except OSError: raise SystemExit(2)
v=os.statvfs(os.path.dirname(p)); print(json.dumps({"apparent":apparent,"allocated":allocated,"fs_free":v.f_bavail*v.f_frsize},sort_keys=True))' "$1"
}

_wc_source_digest() {
  python3 -c 'import hashlib,os,stat,subprocess,sys
root=sys.argv[1]
p=subprocess.run(["git","-C",root,"ls-files","-co","--exclude-standard","-z"],capture_output=True)
if p.returncode: raise SystemExit(2)
h=hashlib.sha256()
for raw in sorted(x for x in p.stdout.split(b"\0") if x):
    rel=os.fsdecode(raw); path=os.path.join(root,rel); st=os.lstat(path)
    h.update(raw+b"\0"+str(st.st_mode).encode()+b"\0")
    if stat.S_ISLNK(st.st_mode): h.update(os.fsencode(os.readlink(path)))
    elif stat.S_ISREG(st.st_mode):
        with open(path,"rb") as fh:
            for chunk in iter(lambda:fh.read(1024*1024),b""): h.update(chunk)
    else: raise SystemExit(3)
    h.update(b"\0")
print(h.hexdigest())' "$1"
}

_wc_removed_receipt() {
  export WC_DECISION="$1" WC_BEFORE="$2"
  python3 -c 'import json,os
b=json.loads(os.environ["WC_BEFORE"]); v=os.statvfs(os.path.dirname(os.environ["CANDIDATE"])); after=v.f_bavail*v.f_frsize
print(json.dumps({"decision":os.environ["WC_DECISION"],"path":os.environ["CANDIDATE"],"apparent_bytes_before":b["apparent"],"allocated_bytes_before":b["allocated"],"apparent_bytes_after":0,"allocated_bytes_after":0,"filesystem_free_before":b["fs_free"],"filesystem_free_after":after,"filesystem_free_delta":after-b["fs_free"]},sort_keys=True))'
}

_wc_gate_applies() {
  case "$1" in
    cache-immediate-parent-manifest)
      _wc_is_cache ;;
    clean)
      if _wc_is_cache; then return 1; else return 0; fi ;;
    *) return 0 ;;
  esac
}

_wc_run_gates() {
  local name="" chunk="" line
  while IFS= read -r line; do
    case "$line" in
      "# gate "*)
        if [ -n "$chunk" ] && _wc_gate_applies "$name"; then
          eval "$chunk" || return 1
        fi
        name="${line#\# gate }"; chunk="" ;;
      *) chunk="$chunk$line
" ;;
    esac
  done < <(_wc_gate_block)
  if [ -n "$chunk" ] && _wc_gate_applies "$name"; then
    eval "$chunk" || return 1
  fi
  return 0
}

_wc_join() {
  # R6.5 reads the authoritative same-owner task set through complete keyset
  # pages. R7 independently reads the filtered trigger audit through complete
  # keyset pages. Called before gates and again immediately before action.
  local tasks="$WC_TMP/peer-tasks.json" trigger="$WC_TMP/trigger-audit.json"
  local ids="$WC_TMP/peer-ids.tsv" tid kind join_digest
  if ! happyranch tasks --org "$ORG" --agent "$AGENT" --limit 1000 \
        --all-pages --json > "$tasks" 2>/dev/null; then
    { _wc_refuse "peer_history_unavailable"; return 2; }
  fi
  if ! happyranch audit --org "$ORG" --agent "$AGENT" \
        --action workspace_cleanup_triggered --limit 1000 --all-pages --json \
        > "$trigger" 2>/dev/null; then
    { _wc_refuse "trigger_history_unavailable"; return 2; }
  fi
  if ! python3 -c 'import json,sys
tasks=json.load(open(sys.argv[1])); audit=json.load(open(sys.argv[2])); acting=sys.argv[3]; agent=sys.argv[4]
if not isinstance(tasks,list) or not isinstance(audit,list): raise SystemExit(2)
trigger_ids=[]
for row in audit:
    if not isinstance(row,dict) or row.get("action")!="workspace_cleanup_triggered" or row.get("agent")!=agent: raise SystemExit(3)
    tid=row.get("task_id")
    if not isinstance(tid,str) or not tid.startswith("TASK-"): raise SystemExit(3)
    trigger_ids.append(tid)
triggers=set(trigger_ids); seen=set()
manual="HAPPYRANCH SYSTEM WORKSPACE CLEANUP RUN (manual-dispatch)"; daemon="HAPPYRANCH SYSTEM WORKSPACE CLEANUP RUN (daemon-triggered)"
for row in tasks:
    if not isinstance(row,dict): raise SystemExit(4)
    tid=row.get("task_id"); brief=row.get("brief"); status=row.get("status"); owner=row.get("assigned_agent")
    if not isinstance(tid,str) or not isinstance(brief,str) or not isinstance(status,str) or owner!=agent or tid in seen: raise SystemExit(4)
    seen.add(tid); first=brief.splitlines()[0] if brief.splitlines() else ""; triggered=tid in triggers
    if triggered and first!=daemon: raise SystemExit(5)
    if first==daemon and not triggered: raise SystemExit(5)
    if tid==acting or first not in (manual,daemon): continue
    kind=("nonterminal" if status not in ("completed","failed","cancelled","superseded") else ("scheduled" if triggered else "manual"))
    print(tid+"\t"+kind)
if any(t not in seen and t!=acting for t in triggers): raise SystemExit(6)' \
        "$tasks" "$trigger" "${ACTING_TASK:-}" "$AGENT" > "$ids"; then
    { _wc_refuse "peer_history_malformed_or_incomplete"; return 2; }
  fi
  local occ_terminal=0
  while IFS="$(printf '\t')" read -r tid kind; do
    [ -n "$tid" ] || continue
    case "$kind" in
      scheduled) occ_terminal=$((occ_terminal+1)) ;;
      manual) ;;
      nonterminal) { _wc_refuse "nonterminal_peer:$tid"; return 2; } ;;
      *) { _wc_refuse "peer_record_incomplete:$tid"; return 2; } ;;
    esac
  done < "$ids"
  if [ "$occ_terminal" -lt 2 ]; then
    { _wc_refuse "report_only_ordinal:$occ_terminal" "report_only"; return 2; }
  fi
  join_digest="$(python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$ids")" || return 2
  if [ -n "${WC_JOIN_DIGEST:-}" ] && [ "$WC_JOIN_DIGEST" != "$join_digest" ]; then
    { _wc_refuse "peer_history_changed"; return 2; }
  fi
  WC_JOIN_DIGEST="$join_digest"; export WC_JOIN_DIGEST
  return 0
}

run_cleanup_candidate() {
  CANDIDATE="${1:?candidate}"; CONTAINING="${2:-$1}"
  export CANDIDATE CONTAINING
  case "${CLEANUP_MARKER:-}" in
    "HAPPYRANCH SYSTEM WORKSPACE CLEANUP RUN (manual-dispatch)"|\
    "HAPPYRANCH SYSTEM WORKSPACE CLEANUP RUN (daemon-triggered)") ;;
    *) { _wc_refuse "marker_not_exact" "inventory_only"; return 2; } ;;
  esac
  if [ -z "$WORKSPACE" ] || [ -z "$PRIMARY" ] || [ -z "$AGENT" ] || \
       [ -z "$ORG" ] || [ -z "${ACTING_TASK:-}" ] || [ -z "${SESSION_ID:-}" ]; then
    { _wc_refuse "scope_unset" "inventory_only"; return 2; }
  fi
  WC_JOIN_DIGEST=""; export WC_JOIN_DIGEST

  TASK="$(basename "$CONTAINING")"
  case "$TASK" in
    TASK-*) ;;
    *) { _wc_refuse "task_mapping"; return 2; } ;;
  esac
  local branch
  if ! branch="$(git -C "$CONTAINING" rev-parse --abbrev-ref HEAD 2>/dev/null)"; then
    { _wc_refuse "containing_unreadable"; return 2; }
  fi
  if [ "$branch" != "task/$TASK" ]; then
    { _wc_refuse "task_mapping_branch"; return 2; }
  fi

  if ! WC_TMP="$(mktemp -d "${TMPDIR:-/tmp}/wc.XXXXXX")"; then
    { _wc_refuse "scratch"; return 2; }
  fi
  TASK_JSON="$WC_TMP/task.json"; export TASK_JSON
  if ! happyranch recall --org "$ORG" "$TASK" > "$TASK_JSON" 2>/dev/null; then
    { _wc_refuse "authoritative_task_unavailable"; return 2; }
  fi
  local owner task_status bytes_before cache_status_before cache_source_before
  owner="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("assigned_agent") or "")' "$TASK_JSON")"
  task_status="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("status") or "")' "$TASK_JSON")"
  if [ "$owner" != "$AGENT" ]; then
    { _wc_refuse "owner_mismatch"; return 2; }
  fi
  case "$task_status" in
    completed|failed|cancelled|superseded) ;;
    *) { _wc_refuse "target_nonterminal"; return 2; } ;;
  esac
  if _wc_is_cache; then AGE_SECONDS=86400; else AGE_SECONDS=604800; fi
  export AGE_SECONDS

  if ! _wc_join; then return 2; fi
  if ! _wc_run_gates; then
    { _wc_refuse "eligibility_gate"; return 2; }
  fi
  if ! _wc_join; then return 2; fi
  # The same-context fresh scan and every other gate immediately precede the
  # literal action. Unknown/refusal never falls through to mutation.
  if ! _wc_run_gates; then
    { _wc_refuse "pre_action_eligibility_gate"; return 2; }
  fi

  if ! bytes_before="$(_wc_measure_path "$CANDIDATE")"; then
    { _wc_refuse "pre_action_measurement_failed"; return 2; }
  fi

  case "$(basename "$CANDIDATE")" in
    node_modules|.venv)
      cache_status_before="$WC_TMP/cache-status-before"
      if ! git -C "$CONTAINING" status --porcelain=v1 -z > "$cache_status_before"; then
        { _wc_refuse "cache_status_unavailable"; return 2; }
      fi
      if ! cache_source_before="$(_wc_source_digest "$CONTAINING")"; then
        { _wc_refuse "cache_source_digest_unavailable"; return 2; }
      fi
      if ! _wc_snapshot_tree; then
        { _wc_refuse "action_boundary_changed"; return 2; }
      fi
      if ! _wc_isolate_cache; then
        _wc_restore_isolated_cache >/dev/null 2>&1 || :
        { _wc_refuse "action_isolation_changed"; return 2; }
      fi
      if ! _wc_delete_isolated_cache; then
        if [ ! -e "${WC_DELETE_STARTED:-}" ]; then
          _wc_restore_isolated_cache >/dev/null 2>&1 || :
        fi
        { _wc_refuse "action_failed"; return 2; }
      fi
      if [ -e "$WC_ISOLATED_CANDIDATE" ] || [ -L "$WC_ISOLATED_CANDIDATE" ]; then
        _wc_restore_isolated_cache >/dev/null 2>&1 || :
        { _wc_refuse "action_residual"; return 2; }
      fi
      if [ -e "$WC_ISOLATION_DIR" ] || [ -L "$WC_ISOLATION_DIR" ]; then
        { _wc_refuse "action_isolation_residual"; return 2; }
      fi
      if ! git -C "$CONTAINING" status --porcelain=v1 -z | cmp -s - "$cache_status_before"; then
        { _wc_refuse "post_action_source_or_status_changed"; return 2; }
      fi
      if [ "$cache_source_before" != "$(_wc_source_digest "$CONTAINING")" ]; then
        { _wc_refuse "post_action_source_or_status_changed"; return 2; }
      fi
      if ! _wc_verify_post_action; then
        { _wc_refuse "post_action_candidate_or_protected_changed"; return 2; }
      fi
      _wc_removed_receipt "removed_cache" "$bytes_before" ;;
    *)
      if ! _wc_snapshot_tree; then
        { _wc_refuse "action_boundary_changed"; return 2; }
      fi
      if ! git -C "$PRIMARY" worktree remove "$CANDIDATE"; then
        { _wc_refuse "action_failed"; return 2; }
      fi
      if ! _wc_verify_post_action; then
        { _wc_refuse "post_action_candidate_or_protected_changed"; return 2; }
      fi
      _wc_removed_receipt "removed_worktree" "$bytes_before" ;;
  esac
  return 0
}
```
<!-- procedure-commands:end -->

## Authorized actions (non-force only)

- **Cache.** Atomically isolate one literal real `node_modules` or `.venv`
  directory inside a same-filesystem private sibling boundary, revalidate its
  inode/tree/owner/device and the protected-set identity against the complete
  action snapshot, then open and recursively remove only that isolated object
  through authenticated no-follow directory descriptors. A replacement before
  descriptor admission refuses without deletion; unsupported descriptor-
  relative primitives refuse before mutation. The cache must be
  inside a registered, non-primary linked worktree of your own workspace, and
  removal is allowed only when its
  immediate parent has the accepted lock/manifest, the owning task has been
  terminal past the 24-hour floor, durable preservation/no-open-or-unmerged-PR
  evidence clears, and the exact host-job current-use receipt clears. The
  containing worktree may otherwise be dirty; preserve its source bytes and
  exact Git status and never remove or clean that worktree.
- **Whole worktree.** After seven terminal days, remove one clean registered
  non-primary worktree with exactly:

  ```
  git -C <primary> worktree remove <literal-path>
  ```

  **Never** `--force`. Never use `rm -rf`, a glob, a parent root, or
  `git clean` to remove a worktree; `git worktree prune` is allowed only for an
  already-missing registered path after a dry-run confirms the exact stale
  record. Cache recursion uses only the descriptor-rooted primitive above,
  never `rm -rf` or a later standalone pathname dispatch. A changed or
  reappearing original candidate path, isolation residue, or identity mismatch
  fails stopped and never emits `removed_cache`.

The procedure's JSON action receipt records the literal path, apparent and
allocated bytes before/after, filesystem free space before/after, and the
concurrent/unattributed filesystem delta. Record literal argv and exit status
beside that receipt and perform protected-path postchecks. Stop further
mutations on any action, evidence, or report failure.

This **implementation and witness task** deletes no production residue, never
deploys, restarts, activates a flag, or sweeps. That prohibition belongs to this
delivery task; the separately authorized future daily/manual use of this skill
retains exactly the narrow cache/worktree actions and protections above, under
the same action-time gates.

## Reporting

Complete through the normal task contract, creating `output/<task_id>/` with
`inventory.json`, `final-ledger.jsonl`, and `report.md` (measured sizes, exact
removals or zero removals, skips and reasons, and any ambiguity), and report to
the founder in the per-agent cleanup thread.
