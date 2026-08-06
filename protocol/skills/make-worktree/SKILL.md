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
PRIMARY_ROOT=$(cd ../../.. && pwd -P)      # canonical absolute primary checkout root
echo "WORKTREE_ROOT=$WORKTREE_ROOT"
echo "PRIMARY_ROOT=$PRIMARY_ROOT"

# 3. Locate the guard script — delivered alongside this skill file.
#    The skill is injected into .claude/skills/make-worktree/ (Claude)
#    or .agents/skills/make-worktree/ (AGENTS.md-based executors).
#    The workspace root is 5 parents above the task worktree
#    (<workspace>/repos/<repo>/.claude/worktrees/<task_id>):
WORKSPACE_ROOT=$(cd "$WORKTREE_ROOT/../../../../.." && pwd -P)
#    The primary checkout root is 3 parents above the task worktree
#    (<primary>/.claude/worktrees/<task_id> → cd ../../.. → <primary>):
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
#    For HappyRanch repos (containing scripts/local_ci.sh and
#    scripts/hooks/pre-push.local-ci.sample), setup also AUTOMATICALLY
#    installs a mandatory pre-push hook via git config --worktree
#    core.hooksPath scoped to this linked worktree only. See the
#    "Pre-push hook (automatic)" section below.
python "$GUARD" setup \
    --worktree-root "$WORKTREE_ROOT" \
    --primary-root "$PRIMARY_ROOT" \
    --task-id "<task_id>"

# 5. All subsequent repo commands MUST use $WORKTREE_ROOT:
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
python "$GUARD" verify --worktree-root "$WORKTREE_ROOT"
```

A successful verification prints `GUARD PASS`. A failure prints a loud
diagnostic naming the primary root, the task worktree root, every
changed primary-checkout path (categorized as tracked, staged, or
untracked), and preservation-first recovery instructions that use
safe `git diff` inspection and `patch` application — no destructive
`git checkout`, `git reset`, or `rm` commands are ever suggested.

The guard does NOT inspect the task worktree diff — a zero-diff task
passes when the primary checkout is unchanged. Edits in the task
worktree are expected and never falsely accused.

## Pre-push hook (automatic)

For HappyRanch repos — those containing both `scripts/local_ci.sh` and
`scripts/hooks/pre-push.local-ci.sample` — `cmd_setup` automatically
installs a mandatory pre-push hook as part of worktree provisioning.

**Scope and safety:**
- The hook is installed ONLY for this linked worktree via `git config
  --worktree core.hooksPath`. The primary/normal checkout's existing
  hook or hooks path is never touched, overwritten, or reconfigured.
- The hook lives under the worktree's Git metadata directory
  (`git rev-parse --git-dir` → `happyranch-hooks/pre-push`), not
  `.git/hooks` and not in the tracked repository tree.
- Setup fails closed with an actionable diagnostic if any mandatory
  step (hook directory creation, file copy, chmod, or config write)
  cannot complete.
- Outside HappyRanch repos, `cmd_setup` is a no-op for hook
  installation — behavior is unchanged.

**What the hook runs:** `scripts/local_ci.sh all` (python + web, mirrors
GitHub PR CI). It does NOT run integration tests. If any step fails the
push is blocked.

**Engineering constraints:**
- The hook CANNOT prevent `git push --no-verify`, which engineering
  policy prohibits. `--no-verify` bypasses hooks, not the gate policy.
- GitHub CI remains the authoritative clean-environment / matrix gate.
  Local-CI is pre-push feedback only.
- Human normal-checkout installation remains opt-in via manual copy of
  the sample hook.

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
