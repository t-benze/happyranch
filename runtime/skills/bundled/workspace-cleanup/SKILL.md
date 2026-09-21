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
  retention floor (24 hours for a cache, 7 days for a whole worktree).
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

Before acting, run the bundled read-only helper:

```
python3 scripts/check_path_use.py --target <literal-path> --json
```

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
- Per thread, the helper checks `cwd`, `root`, `exe`, `maps`, and the private
  FD table, and verifies cross-mount-namespace path identity.

This is a **snapshot** with a disclosed later-opener/write-interruption and
data-loss residual risk. It is not a claim of OS-wide absence or future
non-use, and it is not executable-identity authentication.

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
  `git clean`; `git worktree prune` is allowed only for an
  already-missing registered path after a dry-run confirms the exact stale
  record.

Record literal argv, exit status, apparent and allocated bytes before/after,
filesystem free space before/after with any concurrent/unattributed delta
separately, and protected-path postchecks. Stop further mutations on any action,
evidence, or report failure. This skill never deletes production residue, never
deploys, restarts, activates a flag, or sweeps.

## Reporting

Complete through the normal task contract, creating `output/<task_id>/` with
`inventory.json`, `final-ledger.jsonl`, and `report.md` (measured sizes, exact
removals or zero removals, skips and reasons, and any ambiguity), and report to
the founder in the per-agent cleanup thread.
