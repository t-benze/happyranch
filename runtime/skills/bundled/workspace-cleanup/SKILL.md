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
  statuses — the manual marker, the daemon marker, and trigger-history rows —
  and omit only your own acting task. Any nonterminal peer is a skip. Recheck
  after claim and again immediately before every action.
- **R7 (prior occurrences).** Require exactly two distinct prior joined terminal
  scheduled occurrences. Duplicates collapse. Missing details or a saturated
  finite listing is a skip.
- **Cooperative overlap.** Perform the peer and listing recheck after claim and
  immediately before each action. This is cooperative, not a lock. A new peer
  appearing in the window makes the action a skip.

## Ownership and protected paths

Only a **registered, non-primary, clean** worktree whose durable `HEAD` is
reachable from an approved durable ref (normally `origin/main`), with no open or
unmerged PR for its branch, and no protection, may be considered. Refuse
cross-owner, protected, symlink/shared/unknown, dirty, local-only or unreachable
commits, and insufficient age. Never rewrite, move, archive, bundle, tag,
quarantine, or repair preserved work.

## Current-use observation

Before acting, run the bundled read-only helper. A whole-worktree candidate is
passed as its own target. A literal `node_modules`/`.venv` cache candidate MUST
also resolve to its containing **registered** worktree so that use anywhere in that
worktree (for example a process whose `cwd` is a sibling `src/`) blocks even
though the cache directory itself is untouched:

```
python3 scripts/check_path_use.py --target <literal-path> --containing-worktree <containing-worktree-path> --json
```

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
- Confirmed exit (PID/TID vanished) is distinct from `EACCES`/`EPERM`, PID/TID
  reuse, and changed real/effective/saved/fs credentials — those are `unknown`.
  A missing per-thread `stat`/`status`, an unparseable starttime, or a denied
  thread-status read is an incomplete identity, never a clean result.
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
(`non-primary`, `registration`, `clean`, `durable-head`, `no-open-pr`) inspect
`$CONTAINING`; per-path gates (`workspace-scope`, `ownership`, `not-symlink`,
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
python3 -c 'import os,sys; c=os.path.abspath(sys.argv[1]); w=os.path.abspath(sys.argv[2]); cr=os.path.realpath(c); wr=os.path.realpath(w); cache=os.path.basename(c) in ("node_modules",".venv"); inside=os.path.commonpath((cr,wr))==wr if cr and wr else False; sys.exit(0 if c==cr and w==wr and (c==w or (cache and inside and c!=w)) else 1)' "$CANDIDATE" "$CONTAINING"
# gate non-primary
python3 -c 'import os,sys; sys.exit(0 if os.path.realpath(sys.argv[1])!=os.path.realpath(sys.argv[2]) else 1)' "$CONTAINING" "$PRIMARY"
# gate registration
python3 -c 'import os,subprocess,sys; p=subprocess.run(["git","-C",sys.argv[1],"worktree","list","--porcelain"],capture_output=True,text=True); want=os.path.realpath(sys.argv[2]); sys.exit(1) if p.returncode else None; sys.exit(0 if any(os.path.realpath(l[9:])==want for l in p.stdout.splitlines() if l.startswith("worktree ")) else 1)' "$PRIMARY" "$CONTAINING"
# gate ownership
python3 -c 'import json,os,sys; d=json.load(open(os.environ["TASK_JSON"])); sys.exit(0 if d.get("assigned_agent")==sys.argv[1] else 1)' "$AGENT"
# gate not-symlink
python3 -c 'import os,pathlib,sys; p=pathlib.Path(os.path.abspath(sys.argv[1])); w=os.path.abspath(sys.argv[2]).rstrip("/"); anc=[str(p)]+[str(p.parents[i]) for i in range(len(p.parents))]; ins=[a for a in anc if a==w or a.startswith(w+"/")]; sys.exit(0 if all(not os.path.islink(a) for a in ins) else 1)' "$CANDIDATE" "$WORKSPACE"
# gate same-filesystem
python3 -c 'import os,sys; sys.exit(0 if os.stat(sys.argv[1]).st_dev==os.stat(sys.argv[2]).st_dev else 1)' "$CANDIDATE" "$PRIMARY"
# gate clean
python3 -c 'import subprocess,sys; p=subprocess.run(["git","-C",sys.argv[1],"status","--porcelain"],capture_output=True,text=True); sys.exit(1 if p.returncode or p.stdout else 0)' "$CONTAINING"
# gate durable-head
git -C "$CONTAINING" merge-base --is-ancestor HEAD origin/main
# gate no-open-pr
python3 -c 'import subprocess,sys; p=subprocess.run(["git","-C",sys.argv[1],"remote","get-url","origin"],capture_output=True,text=True); url=p.stdout.strip() if p.returncode==0 else ""; parts=url.split("github.com",1); slug=(parts[1].strip("/:").strip() if len(parts)==2 else ""); slug=(slug[:-4] if slug.endswith(".git") else slug); b=subprocess.run(["git","-C",sys.argv[2],"rev-parse","--abbrev-ref","HEAD"],capture_output=True,text=True); q=(subprocess.run(["gh","pr","list","--repo",slug,"--head",b.stdout.strip(),"--state","open","--json","number","--jq","length"],capture_output=True,text=True) if slug and b.returncode==0 and b.stdout.strip() not in ("","HEAD") else None); sys.exit(1 if q is None or q.returncode else (1 if int(q.stdout.strip() or "1")!=0 else 0))' "$PRIMARY" "$CONTAINING"
# gate retention-age
python3 -c 'import datetime,json,os,sys; d=json.load(open(os.environ["TASK_JSON"])); ca=d.get("completed_at"); term=("completed","failed","cancelled","superseded"); sys.exit(1) if d.get("status") not in term or not ca else None; t=datetime.datetime.fromisoformat(str(ca).replace("Z","+00:00")); t=(t if t.tzinfo else t.replace(tzinfo=datetime.timezone.utc)); age=(datetime.datetime.now(datetime.timezone.utc)-t).total_seconds(); sys.exit(0 if age>=float(sys.argv[1]) else 1)' "$AGE_SECONDS"
# gate cache-immediate-parent-manifest
test -f "$(dirname "$CANDIDATE")/package-lock.json" || test -f "$(dirname "$CANDIDATE")/pnpm-lock.yaml" || test -f "$(dirname "$CANDIDATE")/yarn.lock" || test -f "$(dirname "$CANDIDATE")/uv.lock" || test -f "$(dirname "$CANDIDATE")/poetry.lock" || test -f "$(dirname "$CANDIDATE")/requirements.txt"
# gate current-use-scan
python3 "$SKILL/scripts/check_path_use.py" --target "$CANDIDATE" --containing-worktree "$CONTAINING" --json
```
<!-- eligibility-commands:end -->

`cache-immediate-parent-manifest` applies only to a `node_modules`/`.venv`
cache; skip it for a whole worktree. Every other gate applies to both. A
non-exempt unreadable same-user process, a missing/ambiguous containing
worktree, or any saturated listing makes the scan `unknown` -> skip; a positive
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

_wc_gate_applies() {
  case "$1" in
    cache-immediate-parent-manifest)
      case "$(basename "$CANDIDATE")" in node_modules|.venv) return 0;; *) return 1;; esac ;;
    *) return 0 ;;
  esac
}

_wc_run_gates() {
  local name="" chunk="" line
  while IFS= read -r line; do
    case "$line" in
      "# gate "*)
        if [ -n "$chunk" ] && _wc_gate_applies "$name"; then
          bash -c "$chunk" || return 1
        fi
        name="${line#\# gate }"; chunk="" ;;
      *) chunk="$chunk$line
" ;;
    esac
  done < <(_wc_gate_block)
  if [ -n "$chunk" ] && _wc_gate_applies "$name"; then
    bash -c "$chunk" || return 1
  fi
  return 0
}

_wc_join() {
  # R7 prior joined terminal scheduled occurrences + R6.5 manual/daemon/
  # trigger-history peers. Both finite audit lists are capped at 1001 so the
  # 1001st row is a saturation sentinel; every referenced task is joined by
  # exact id. Called before the gates and again immediately before every action.
  local all="$WC_TMP/all-audit.json" trigger="$WC_TMP/trigger-audit.json"
  local ids="$WC_TMP/peer-ids.tsv" tid triggered tjson kind rc
  if ! happyranch audit --org "$ORG" --agent "$AGENT" --limit 1001 \
        --json > "$all" 2>/dev/null; then
    { _wc_refuse "peer_history_unavailable"; return 2; }
  fi
  if ! happyranch audit --org "$ORG" --agent "$AGENT" \
        --action workspace_cleanup_triggered --limit 1001 --json \
        > "$trigger" 2>/dev/null; then
    { _wc_refuse "trigger_history_unavailable"; return 2; }
  fi
  if ! python3 -c 'import json,sys
def load(path, strict):
    data=json.load(open(path))
    if not isinstance(data,list) or len(data)>=1001: raise ValueError("saturated")
    out=[]
    for row in data:
        if not isinstance(row,dict): raise ValueError("malformed")
        tid=row.get("task_id")
        if tid is None and not strict: continue
        if not isinstance(tid,str) or not tid.startswith("TASK-"):
            if strict: raise ValueError("bad trigger id")
            continue
        out.append(tid)
    return out
all_ids=load(sys.argv[1],False); trigger_ids=load(sys.argv[2],True)
seen=[]
for tid in all_ids+trigger_ids:
    if tid!=sys.argv[3] and tid not in seen: seen.append(tid)
triggers=set(trigger_ids)
for tid in seen: print(tid+"\t"+("1" if tid in triggers else "0"))' \
        "$all" "$trigger" "${ACTING_TASK:-}" > "$ids"; then
    { _wc_refuse "peer_history_malformed_or_saturated"; return 2; }
  fi
  local occ_terminal=0
  while IFS="$(printf '\t')" read -r tid triggered; do
    [ -n "$tid" ] || continue
    tjson="$WC_TMP/$tid.json"
    if ! happyranch recall --org "$ORG" "$tid" > "$tjson" 2>/dev/null; then
      { _wc_refuse "peer_join_unavailable:$tid"; return 2; }
    fi
    kind="$(python3 -c 'import json,sys
d=json.load(open(sys.argv[1])); tid=sys.argv[2]; agent=sys.argv[3]; triggered=sys.argv[4]=="1"
if not isinstance(d,dict) or d.get("task_id")!=tid or not isinstance(d.get("brief"),str) or not isinstance(d.get("status"),str) or not isinstance(d.get("assigned_agent"),str): sys.exit(4)
if d["assigned_agent"]!=agent: print("foreign"); sys.exit(0)
first=d["brief"].splitlines()[0] if d["brief"].splitlines() else ""
manual="HAPPYRANCH SYSTEM WORKSPACE CLEANUP RUN (manual-dispatch)"
daemon="HAPPYRANCH SYSTEM WORKSPACE CLEANUP RUN (daemon-triggered)"
if (triggered and first!=daemon) or (first==daemon and not triggered): sys.exit(5)
if first not in (manual,daemon): print("ordinary"); sys.exit(0)
if d["status"] not in ("completed","failed","cancelled","superseded"): print("nonterminal"); sys.exit(0)
print("scheduled" if triggered else "manual")' "$tjson" "$tid" "$AGENT" "$triggered")"
    rc=$?
    if [ "$rc" -eq 4 ]; then
      { _wc_refuse "peer_record_incomplete:$tid"; return 2; }
    elif [ "$rc" -ne 0 ]; then
      { _wc_refuse "peer_history_conflict:$tid"; return 2; }
    fi
    case "$kind" in
      scheduled) occ_terminal=$((occ_terminal+1)) ;;
      manual|ordinary|foreign) ;;
      nonterminal) { _wc_refuse "nonterminal_peer:$tid"; return 2; } ;;
      *) { _wc_refuse "peer_record_incomplete:$tid"; return 2; } ;;
    esac
  done < "$ids"
  if [ "$occ_terminal" -lt 2 ]; then
    { _wc_refuse "report_only_ordinal:$occ_terminal" "report_only"; return 2; }
  fi
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
  if [ -z "$WORKSPACE" ] || [ -z "$PRIMARY" ] || [ -z "$AGENT" ] || [ -z "$ORG" ]; then
    { _wc_refuse "scope_unset" "inventory_only"; return 2; }
  fi

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
  local owner status
  owner="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("assigned_agent") or "")' "$TASK_JSON")"
  status="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("status") or "")' "$TASK_JSON")"
  if [ "$owner" != "$AGENT" ]; then
    { _wc_refuse "owner_mismatch"; return 2; }
  fi
  case "$status" in
    completed|failed|cancelled|superseded) ;;
    *) { _wc_refuse "target_nonterminal"; return 2; } ;;
  esac

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

  case "$(basename "$CANDIDATE")" in
    node_modules|.venv)
      if ! rm -rf -- "$CANDIDATE"; then
        { _wc_refuse "action_failed"; return 2; }
      fi
      printf '{"decision":"removed_cache","path":"%s"}\n' "$CANDIDATE" ;;
    *)
      if ! git -C "$PRIMARY" worktree remove "$CANDIDATE"; then
        { _wc_refuse "action_failed"; return 2; }
      fi
      printf '{"decision":"removed_worktree","path":"%s"}\n' "$CANDIDATE" ;;
  esac
  return 0
}
```
<!-- procedure-commands:end -->

## Authorized actions (non-force only)

- **Cache.** Remove one literal real `node_modules` or `.venv` directory inside a
  registered, non-primary linked worktree of your own workspace, only when its
  immediate parent has the accepted lock/manifest, the owning task has been
  terminal past the 24-hour floor, and the current-use scan clears.
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

Record literal argv, exit status, apparent and allocated bytes before/after,
filesystem free space before/after with any concurrent/unattributed delta
separately, and protected-path postchecks. Stop further mutations on any action,
evidence, or report failure.

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
