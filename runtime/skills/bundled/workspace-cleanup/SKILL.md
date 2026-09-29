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
accepted durable ref (normally `origin/main`); a freshly verified owning-task
remote branch on `origin` whose head contains (is equal to or descends from)
the candidate `HEAD`; or one unambiguous confirmed merged PR for that same task
branch whose head contains the candidate `HEAD`. Missing, stale, unfetchable,
diverged, conflicting, or malformed remote/PR evidence refuses. A confirmed
merged PR preserves the integrated content but may not preserve the original
commit topology. Refuse
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

The executable forms of these gates live only in
`scripts/run_cleanup_candidate.sh`; this document does not carry a second
copy. The shipped script executes them directly in the documented order.

`cache-immediate-parent-manifest` applies only to a `node_modules`/`.venv`
cache; skip it for a whole worktree. `clean` applies only to a whole worktree;
every other gate applies to both. A
non-exempt unreadable same-user process, a missing/ambiguous containing
worktree, or any incomplete listing makes the scan `unknown` -> skip; a positive
non-exempt use makes it `blocked` -> skip; only `clear_observation` with every
gate exit 0 permits the non-force action.

## Delivered procedure (executable control flow)

Execute the bundled Bash program directly; do not copy, source, extract, or
evaluate commands from this Markdown file:

```bash
bash "$SKILL/scripts/run_cleanup_candidate.sh" "$CANDIDATE" "$CONTAINING"
```

`CANDIDATE` is the literal cache or worktree path and `CONTAINING` is its
registered containing worktree (the same path for a whole-worktree candidate).
The script uses the environment contract above, prints one JSON receipt, and
returns `0` only when the literal action ran or `2` on refusal. A refusal before
the literal action begins performs no mutation; a failure after action starts
may already have partially mutated the candidate as described above.

## Authorized actions (non-force only)

- **Cache.** Atomically isolate one literal real `node_modules` or `.venv`
  directory inside a same-filesystem private sibling boundary, revalidate its
  inode/tree/owner/device and the protected-set identity against the complete
  action snapshot, then open and recursively remove only that isolated object
  through authenticated no-follow directory descriptors. A replacement before
  descriptor admission refuses without deletion. Descriptor-relative primitive
  support is checked only after the candidate has been moved into private
  isolation: an unsupported platform refuses before recursive deletion, then
  the caller attempts restoration. Restoration may fail and leave the candidate
  in isolation; the refusal never emits `removed_cache`. The cache must be
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
