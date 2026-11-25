---
name: make-worktree
description: Use this skill before any git commit, git checkout, or file edit inside repos/<name>/. Read-only exploration does not need a worktree. Manages a per-task git worktree at .claude/worktrees/<task_id>/ on branch task/<task_id>. Activates a worktree-root guard that prevents accidental primary-checkout edits.
---

# make-worktree

Use `$TMPDIR` for disposable payloads and browser profiles (shell fallback:
`${TMPDIR:-/tmp}`); linked worktrees still belong only in the canonical slot
below. Keep database data under `$TMPDIR` too; Unix socket path limits and the
permitted short socket-only fallback are documented in the start-task skill.

Per Claude Code convention, worktrees live inside the repo at `.claude/worktrees/<task_id>/` and use a branch named `task/<task_id>`. The read-only review recipe below may detach only after normal guard setup on that branch.

## When to invoke

Before any operation in `repos/<repo_name>/` that mutates state:
- `git commit`
- `git checkout`
- file edits (Write, Edit)

Read-only operations (Read, Grep, Glob) do not need a worktree.

## Setup

```bash
# 1. Fetch the requested base and create the worktree from its remote ref
REPO_NAME="<repo_name>"
cd "repos/$REPO_NAME"
git fetch origin <base>
git rev-parse origin/<base>               # record this base SHA
mkdir -p .claude/worktrees
git worktree add .claude/worktrees/<task_id> -b task/<task_id> origin/<base>
# Never reset the primary checkout.

# 2. Compute canonical absolute roots
cd .claude/worktrees/<task_id>
WORKTREE_ROOT=$(pwd -P)                   # canonical absolute worktree root
#    The primary checkout root is 3 parents above the task worktree
#    (<primary>/.claude/worktrees/<task_id> → cd ../../.. → <primary>):
PRIMARY_ROOT=$(cd ../../.. && pwd -P)      # canonical absolute primary checkout root
echo "WORKTREE_ROOT=$WORKTREE_ROOT"
echo "PRIMARY_ROOT=$PRIMARY_ROOT"

# 3. Locate the guard script — delivered alongside this skill file.
#    The skill is linked under BOTH .claude/skills/make-worktree/
#    and .agents/skills/make-worktree/ for every executor.
#    The workspace root is 5 parents above the task worktree
#    (<workspace>/repos/<repo>/.claude/worktrees/<task_id>):
WORKSPACE_ROOT=$(cd "$WORKTREE_ROOT/../../../../.." && pwd -P)
GUARD="$WORKSPACE_ROOT/.claude/skills/make-worktree/worktree_guard.py"
if [ ! -f "$GUARD" ]; then
    GUARD="$WORKSPACE_ROOT/.agents/skills/make-worktree/worktree_guard.py"
fi
if [ ! -f "$GUARD" ]; then
    echo "ERROR: Cannot locate worktree_guard.py in .claude/skills/ or .agents/skills/" >&2
    echo "  Workspace root: $WORKSPACE_ROOT" >&2
    echo "  Expected at: .claude/skills/make-worktree/worktree_guard.py" >&2
    echo "  or:          .agents/skills/make-worktree/worktree_guard.py" >&2
    exit 2
fi

# 4. Activate the worktree-root guard — this snapshots the primary checkout
#    state so later steps can detect accidental primary-checkout edits.
python3 "$GUARD" setup \
    --worktree-root "$WORKTREE_ROOT" \
    --primary-root "$PRIMARY_ROOT" \
    --task-id "<task_id>"

# 5. All subsequent repo commands MUST use $WORKTREE_ROOT:
#    cd "$WORKTREE_ROOT"
#    scripts/local_ci.sh all  # lint/static only; daily E2E is separate (THR-291 seq69)
#    git -C "$WORKTREE_ROOT" diff --stat
```

Shell variables do not persist between Bash calls. Copy the absolute
`WORKTREE_ROOT` and `PRIMARY_ROOT` printed by setup and use those literal paths
in later calls. To re-derive `GUARD` in a new call (replace `<workspace-root>`
with its absolute path):

```bash
GUARD="<workspace-root>/.claude/skills/make-worktree/worktree_guard.py"; [ -f "$GUARD" ] || GUARD="<workspace-root>/.agents/skills/make-worktree/worktree_guard.py"
```

**After setup, absolute `repos/<repo>/...` paths are FORBIDDEN.**
Every Read, Write, Edit, grep, glob, git, and test command that touches
repo files MUST use `$WORKTREE_ROOT` (the worktree path) or a relative
path rooted at the worktree. `repos/<repo>/` is the primary checkout
(`$PRIMARY_ROOT`); editing it is the cause of false-green verification. Use of
the primary-checkout path that results in a guard failure on verify will be
treated as a task defect.

### Ownership on retries and continuations

`setup` requires an explicit, nonempty `--task-id`. The canonical worktree
folder basename must equal that owning task ID and the checked-out branch must
be exactly `task/<task_id>`. Missing IDs, foreign folder/branch IDs, detached or
unavailable branches, and `-final`/renamed paths that disagree fail before any
snapshot, exclude or stale-hook config write. Never omit the ID or substitute
the PR branch's task ID to bypass a mismatch. The read-only review recipe below
retains its original guard after detaching; never run `setup` while detached.

Ownership describes the resource's canonical task folder, not the current
session, retry link or PR. A continuation using an existing worktree keeps its
original owner and baseline: inspect the guard's task ID and canonical roots,
folder and branch, then use `verify` without re-running `setup`. Do not
re-snapshot a predecessor baseline to hide primary drift. If those identities
disagree, stop and report the exact mismatch; do not repair existing guards or
rename resources as a workaround. When a retry needs its own worktree, create
its matching task folder/branch. Reusing a predecessor's PR branch in a new
task folder does not satisfy setup; report that conflict to the manager.

For an existing worktree, use these three steps with literal absolute paths:

1. Run `git -C <primary-root> worktree list --porcelain` and identify the exact
   registered worktree, its folder and `task/<task_id>` branch.
2. Read `<worktree-root>/.worktree-guard.json` and compare `task_id`,
   `worktree_root` and `primary_root` with that registration and its canonical
   roots. Stop and report any mismatch.
3. Run `python3 <absolute-guard-path> verify --worktree-root <worktree-root>`
   against the retained baseline; do not run `setup` again.

## Verify (before test, commit, and report)

Before running tests, committing, or reporting completion, verify that
no accidental primary-checkout edits have occurred:

```bash
python3 "$GUARD" verify --worktree-root "$WORKTREE_ROOT"
```

A successful verification prints `GUARD PASS`. A failure prints a loud
diagnostic naming the primary root, the task worktree root, every
changed tracked, staged or non-ignored untracked primary-checkout path;
gitignored files are not checked. Preservation-first recovery instructions use
`git diff --binary HEAD` and `git apply --3way` with a patch stored in the
worktree alongside the untracked-file tarball. Remove the recovery patch after
successful application and inspection so it is not committed — no destructive
`git checkout`, `git reset`, or `rm` commands are ever suggested.

The guard does NOT inspect the task worktree diff — a zero-diff task
passes when the primary checkout is unchanged. Edits in the task
worktree are expected and never falsely accused.

On setup, the guard writer best-effort adds the exact root-only rule
`/.worktree-guard.json` to the clone's shared Git common-dir `info/exclude`,
creating `info/` when needed and preserving existing content. Repeated setup
does not duplicate the rule. This makes an untracked root guard invisible to
Git status and non-force worktree removal. Modified tracked or staged guards,
a `.gitignore` negation, subdirectory guards and other untracked files still
count as dirty. If the exclude cannot be written, setup still records and
verifies the guard; without another ignore rule the file remains dirty.
Aliased or unsafe excludes warn and are skipped without changing target bytes:
a symlinked/non-directory `info` parent, a symlinked or multiply linked final
file, or any non-regular final file. Setup and guard enforcement continue.

## Repository Git process

Guard setup and verification do not configure Git hooks. `setup` also clears a
legacy worktree `core.hooksPath` if present. Follow each repository's documented
Git-hook and publication-process requirements.
`git push --no-verify` remains prohibited by engineering policy, and GitHub CI
remains the authoritative clean-environment / matrix gate.

## Concurrency

Two sessions on the same agent role may try to create different worktrees simultaneously. If `git worktree add` fails because of a stale lock, retry once after 1 second.

## Temporary review, QA, and scratch worktrees

Use the reviewing task's **own** canonical worktree slot, not the author's task
ID or a `<task>-<sha>` suffix. This recipe requires the actual owning ID to be
`TASK-<digits>`; never relabel another ID to fit the sweeper's filter. Never use
`/tmp` or `.happyranch/task-tmp` for a linked worktree. A registered worktree in
task-tmp prevents that scratch root's removal.

A reviewer task normally has no maker worktree. If it already has one, preserve
it and report the conflict; do not detach it, reset it, re-snapshot its guard or
allocate a second suffixed slot. An existing branch or admin entry also means
stop and report, even if the folder is absent. Inspect `git worktree list
--porcelain` first; any unavailable ownership evidence means stop.

```bash
# Run from the workspace root.
WORKSPACE_ROOT=$(pwd -P)
PRIMARY_ROOT="$WORKSPACE_ROOT/repos/<repo>"
REVIEW_TASK_ID="<own TASK-id>"
REVIEW_SHA="<full immutable commit SHA>"
WORKTREE_ROOT="$PRIMARY_ROOT/.claude/worktrees/$REVIEW_TASK_ID"
for review_slot in "$WORKTREE_ROOT" "$PRIMARY_ROOT/.git/worktrees/$REVIEW_TASK_ID"; do
    if [ -e "$review_slot" ] || [ -L "$review_slot" ]; then
        echo "STOP: existing worktree/admin slot: $review_slot" >&2
        exit 1
    fi
done
git -C "$PRIMARY_ROOT" fetch origin || exit 1
mkdir -p "$PRIMARY_ROOT/.claude/worktrees" || exit 1
# -b refuses an existing task branch; never use -B or force.
git -C "$PRIMARY_ROOT" worktree add "$WORKTREE_ROOT" -b "task/$REVIEW_TASK_ID" "$REVIEW_SHA" || exit 1
if [ "$(git -C "$WORKTREE_ROOT" rev-parse --absolute-git-dir)" != "$PRIMARY_ROOT/.git/worktrees/$REVIEW_TASK_ID" ]; then
    echo "STOP: unexpected admin mapping; preserve and report" >&2
    exit 1
fi
GUARD="$WORKSPACE_ROOT/.claude/skills/make-worktree/worktree_guard.py"
[ -f "$GUARD" ] || GUARD="$WORKSPACE_ROOT/.agents/skills/make-worktree/worktree_guard.py"
python3 "$GUARD" setup --worktree-root "$WORKTREE_ROOT" --primary-root "$PRIMARY_ROOT" --task-id "$REVIEW_TASK_ID" || exit 1
git -C "$WORKTREE_ROOT" checkout --detach "$REVIEW_SHA" || exit 1
python3 "$GUARD" verify --worktree-root "$WORKTREE_ROOT"
```

Run guard setup while attached, then retain that original baseline and use
`verify` before tests and reporting. Do not edit or commit from the detached
review checkout. Use its absolute path for all review/test commands. Creation,
mapping or guard failure means stop, preserve the result and report the error;
never fall back to another location or fabricate a guard.

The existing nightly sweeper can consider this canonical worktree only after
the reviewing task and its own structural root have ended, with matching
assigned/session workspace evidence and no active root job. Detached HEAD is
supported, but clean status, reachability from a kept branch/remote, recursive
one-hour age, registration, containment and native-writer/hold gates still
apply. End-of-task maker assessment follows Cleanup below; a task-like name or
Unix ownership alone grants no removal authority.

Legacy `.happyranch/scratch/worktrees/<task>-<sha>` checkouts are not nightly
candidates and are not moved or deleted by this change. A valid stable legacy
registration present at discovery does not itself hold the clone; a new
non-TASK admin name during or after discovery can hold the clone. Unsafe or
uncertain legacy metadata can also hold discovery. Any later exact-path owner
removal must satisfy existing cleanup authority and preservation checks. Never
prune or scan legacy worktrees as part of this recipe.

## Cleanup

Before the final completion callback — including blocker/error paths — assess
the exact task-owned worktree. Preserve it when it is dirty, staged, untracked,
committed but not present in a durable remote ref, associated with an open or
closed-unmerged PR,
still referenced by a live session/control/process/fd, explicitly deferred, or
when any identity, ownership, probe, or deadline fact is unavailable or
uncertain. Do not infer eligibility from a task-like directory name.

Use these concrete Git and PR probes:

```bash
git -C <wt> status --porcelain                  # must be empty
git -C <wt> branch -r --contains HEAD           # must be non-empty
gh pr list --head task/<id> --state all --json state,mergedAt
```

An open PR or a closed PR without `mergedAt` requires preservation. These
probes do not establish the other preservation facts above. Any fact you cannot
observe means defer with a specific reason.

Only after every preservation check succeeds, change to the primary checkout
and remove that exact registered path without force:

```bash
cd repos/<repo_name>
git worktree remove .claude/worktrees/<task_id>
```

Never delete the task branch. If a preservation check fails or removal returns
nonzero, leave the worktree and branch intact and add
`worktree-deferred: <reason>` to the `risks` array in the completion JSON file
(the server stores it as `risks_flagged`). Make one bounded attempt only; do not
schedule or perform a retry, force removal, historical scan or cache sweep.
Neither this skill nor the sweeper/cleanup contract authorizes `git worktree prune`.

## Later root-terminal reconsideration

A recorded deferral may later be reconsidered by the runtime; this gives you no extra cleanup authority.
