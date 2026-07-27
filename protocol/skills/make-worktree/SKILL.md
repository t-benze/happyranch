---
name: make-worktree
description: Use this skill before any git commit, git checkout, or file edit inside repos/<name>/. Read-only exploration does not need a worktree. Manages a per-task git worktree at .claude/worktrees/<task_id>/ on branch task/<task_id>. Activates a worktree-root guard that prevents accidental primary-checkout edits.
---

# make-worktree

Per Claude Code convention, worktrees live inside the repo at `.claude/worktrees/<task_id>/` and use a branch named `task/<task_id>`.

## When to invoke

Before any operation in `repos/<repo_name>/` that mutates state:
- `git commit`
- `git checkout`
- file edits (Write, Edit)

Read-only operations (Read, Grep, Glob) do not need a worktree.

## Setup

```bash
# 1. Create the worktree from the primary checkout
REPO_NAME="<repo_name>"
cd repos/$REPO_NAME
mkdir -p .claude/worktrees
git worktree add .claude/worktrees/<task_id> -b task/<task_id>

# 2. Compute canonical absolute roots
cd .claude/worktrees/<task_id>
WORKTREE_ROOT=$(pwd -P)                   # canonical absolute worktree root
PRIMARY_ROOT=$(cd ../.. && pwd -P)        # canonical absolute primary checkout root
echo "WORKTREE_ROOT=$WORKTREE_ROOT"
echo "PRIMARY_ROOT=$PRIMARY_ROOT"

# 3. Activate the worktree-root guard — this snapshots the primary checkout
#    state so later steps can detect accidental primary-checkout edits.
python -m runtime.tools.worktree_guard setup \
    --worktree-root "$WORKTREE_ROOT" \
    --primary-root "$PRIMARY_ROOT" \
    --task-id "<task_id>"

# 4. All subsequent repo commands MUST use $WORKTREE_ROOT:
#    cd "$WORKTREE_ROOT"
#    uv run pytest "$WORKTREE_ROOT/tests/" -v
#    git -C "$WORKTREE_ROOT" diff --stat
```

**After setup, absolute `repos/<repo>/...` paths are FORBIDDEN.**
Every Read, Write, Edit, grep, glob, git, and test command that touches
repo files MUST use `$WORKTREE_ROOT` (the worktree path) or a relative
path rooted at the worktree. The canonical primary checkout root
`$PRIMARY_ROOT` contains the `repos/<repo>/` path — that surface is the
source of every known false-green verification incident. Use of the
primary-checkout path that results in a guard failure on verify will be
treated as a task defect.

## Verify (before test, commit, and report)

Before running tests, committing, or reporting completion, verify that
no accidental primary-checkout edits have occurred:

```bash
python -m runtime.tools.worktree_guard verify --worktree-root "$WORKTREE_ROOT"
```

A successful verification prints `GUARD PASS`. A failure prints a loud
diagnostic naming the primary root, the task worktree root, and every
changed primary-checkout path, plus recovery instructions.

The guard does NOT inspect the task worktree diff — a zero-diff task
passes when the primary checkout is unchanged. Edits in the task
worktree are expected and never falsely accused.

## Concurrency

Two sessions on the same agent role may try to create different worktrees simultaneously. If `git worktree add` fails because of a stale lock, retry once after 1 second.

## Cleanup

At the end of every task — even on blocker/error paths — remove the worktree:

```bash
cd repos/<repo_name>
git worktree remove .claude/worktrees/<task_id> --force
git branch -D task/<task_id> 2>/dev/null || true
```

If cleanup fails (uncommitted changes you wanted to keep), leave the worktree and surface this in the completion report's `risks_flagged`.
