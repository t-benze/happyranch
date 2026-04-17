---
name: make-worktree
description: Use this skill before any git commit, git checkout, or file edit inside repos/<name>/. Read-only exploration does not need a worktree. Manages a per-task git worktree at .claude/worktrees/<task_id>/ on branch task/<task_id>.
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
cd repos/<repo_name>
mkdir -p .claude/worktrees
git worktree add .claude/worktrees/<task_id> -b task/<task_id>
cd .claude/worktrees/<task_id>
# All writes happen here.
```

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
