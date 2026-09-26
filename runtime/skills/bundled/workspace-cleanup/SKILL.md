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
python3 -c 'import os,sys; c=os.path.realpath(sys.argv[1]); w=os.path.realpath(sys.argv[2]).rstrip("/")+"/repos/"; sys.exit(0 if c.startswith(w) else 1)' "$CANDIDATE" "$WORKSPACE"
# gate canonical-shape
python3 -c 'import os,sys; c=os.path.abspath(sys.argv[1]); w=os.path.abspath(sys.argv[2]); cr=os.path.realpath(c); wr=os.path.realpath(w); cache=os.path.basename(c) in ("node_modules",".venv"); immediate=os.path.dirname(c)==w; sys.exit(0 if c==cr and w==wr and (c==w or (cache and immediate and c!=w)) else 1)' "$CANDIDATE" "$CONTAINING"
# gate non-primary
python3 -c 'import os,sys; sys.exit(0 if os.path.realpath(sys.argv[1])!=os.path.realpath(sys.argv[2]) else 1)' "$CONTAINING" "$PRIMARY"
# gate registration
python3 -c 'import os,subprocess,sys; p=subprocess.run(["git","-C",sys.argv[1],"worktree","list","--porcelain"],capture_output=True,text=True); want=os.path.realpath(sys.argv[2]); sys.exit(1) if p.returncode else None; sys.exit(0 if any(os.path.realpath(l[9:])==want for l in p.stdout.splitlines() if l.startswith("worktree ")) else 1)' "$PRIMARY" "$CONTAINING"
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

_wc_git_preserved() {
  local head branch remote_url repo_slug pr_json remote_rows remote_rc
  local durable_ref=0 remote_match=0 merged_match
  head="$(git -C "$CONTAINING" rev-parse HEAD 2>/dev/null)" || return 1
  branch="$(git -C "$CONTAINING" rev-parse --abbrev-ref HEAD 2>/dev/null)" || return 1
  [ "$branch" = "task/$TASK" ] || return 1
  remote_url="$(git -C "$PRIMARY" remote get-url origin 2>/dev/null)" || return 1
  repo_slug="$(python3 -c 'import re,sys
u=sys.argv[1].strip(); m=re.fullmatch(r"(?:https://github[.]com/|git@github[.]com:)([^/]+/[^/]+?)(?:[.]git)?",u); print(m.group(1) if m else "")' "$remote_url")"
  [ -n "$repo_slug" ] || return 1
  pr_json="$WC_TMP/pr-evidence.json"
  if ! gh pr list --repo "$repo_slug" --head "$branch" --state all --limit 100 \
        --json number,state,mergedAt,headRefName,headRefOid > "$pr_json" 2>/dev/null; then
    return 1
  fi
  merged_match="$(python3 -c 'import json,sys
d=json.load(open(sys.argv[1])); branch=sys.argv[2]; head=sys.argv[3]
if not isinstance(d,list): raise SystemExit(2)
merged=[]
for row in d:
    if not isinstance(row,dict) or not isinstance(row.get("number"),int) or row.get("headRefName")!=branch or not isinstance(row.get("state"),str): raise SystemExit(2)
    if row.get("mergedAt") is None: raise SystemExit(3)
    if row.get("headRefOid")!=head: raise SystemExit(4)
    merged.append(row)
if len(merged)>1: raise SystemExit(5)
print("1" if len(merged)==1 else "0")' "$pr_json" "$branch" "$head")" || return 1
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
  local nonce title rationale scan_script payload submit_out wait_json
  local job_id show_file output_file started_epoch
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
  submit_out="$WC_TMP/scan-submit-$nonce.txt"
  if ! happyranch jobs submit --org "$ORG" --from-file "$payload" > "$submit_out" 2>/dev/null; then
    return 1
  fi
  job_id="$(python3 -c 'import re,sys
s=open(sys.argv[1]).read(); m=re.fullmatch(r"ok: submitted (JOB-[0-9]+) [(]status=(?:pending|running|completed)[)]. Self-block your task referencing this ID.\n?",s); print(m.group(1) if m else "")' "$submit_out")"
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
  show_file="$WC_TMP/scan-show-$nonce.txt"
  output_file="$WC_TMP/scan-output-$nonce.txt"
  happyranch jobs show "$job_id" --org "$ORG" > "$show_file" 2>/dev/null || return 1
  happyranch jobs output "$job_id" --stream both --max-bytes 1048576 \
    --org "$ORG" > "$output_file" 2>/dev/null || return 1
  export job_id started_epoch
  if ! python3 -c 'import datetime,os,re,sys
s=open(sys.argv[1]).read(); first=s.splitlines()[0] if s.splitlines() else ""
m=re.fullmatch(re.escape(os.environ["job_id"])+r"   completed   submitted (\S+)",first)
if not m: raise SystemExit(1)
t=datetime.datetime.fromisoformat(m.group(1).replace("Z","+00:00")).timestamp()
need=["Agent:        "+os.environ["AGENT"],"Task:         "+os.environ["ACTING_TASK"],"Interpreter:  bash","Cwd hint:     (workspace root)","Title:        "+os.environ["title"],os.environ["rationale"],"  "+os.environ["scan_script"],"Exit code:    0"]
raise SystemExit(0 if t>=float(os.environ["started_epoch"])-2 and all(x in s for x in need) else 1)' "$show_file"; then
    _wc_refuse "scan_receipt_mismatch" >/dev/null
    return 1
  fi
  if ! python3 -c 'import json,os,sys
s=open(sys.argv[1]).read(); a="--- stdout ---\n"; b="\n--- stderr ---\n"
if not s.startswith(a) or b not in s: raise SystemExit(1)
out,err=s[len(a):].split(b,1)
if err.strip(): raise SystemExit(1)
d=json.loads(out)
ok=isinstance(d,dict) and d.get("state")=="clear_observation" and d.get("target")==os.path.realpath(os.environ["CANDIDATE"])
raise SystemExit(0 if ok else 1)' "$output_file"; then
    _wc_refuse "scan_receipt_mismatch" >/dev/null
    return 1
  fi
  return 0
}

_wc_measure_path() {
  python3 -c 'import json,os,sys
p=sys.argv[1]; apparent=allocated=0
for root,dirs,files in os.walk(p,followlinks=False):
    for name in dirs+files:
        q=os.path.join(root,name)
        try: st=os.lstat(q)
        except OSError: raise SystemExit(2)
        apparent+=st.st_size; allocated+=getattr(st,"st_blocks",0)*512
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
      if ! rm -rf -- "$CANDIDATE"; then
        { _wc_refuse "action_failed"; return 2; }
      fi
      if ! git -C "$CONTAINING" status --porcelain=v1 -z | cmp -s - "$cache_status_before"; then
        { _wc_refuse "post_action_source_or_status_changed"; return 2; }
      fi
      if [ "$cache_source_before" != "$(_wc_source_digest "$CONTAINING")" ]; then
        { _wc_refuse "post_action_source_or_status_changed"; return 2; }
      fi
      _wc_removed_receipt "removed_cache" "$bytes_before" ;;
    *)
      if ! git -C "$PRIMARY" worktree remove "$CANDIDATE"; then
        { _wc_refuse "action_failed"; return 2; }
      fi
      _wc_removed_receipt "removed_worktree" "$bytes_before" ;;
  esac
  return 0
}
```
<!-- procedure-commands:end -->

## Authorized actions (non-force only)

- **Cache.** Remove one literal real `node_modules` or `.venv` directory inside a
  registered, non-primary linked worktree of your own workspace, only when its
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
  record. The cache action above is the only `rm -rf`, and only on a literal
  real `node_modules`/`.venv` candidate path.

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
