# Contributing to HappyRanch web

## Worktree dependency setup

**ALWAYS use an isolated per-worktree `node_modules`. NEVER symlink it into
`repos/happyranch/web/node_modules` (the shared main checkout).**

### Why symlinking is dangerous

Two operations silently wipe the shared checkout when `node_modules` is a
symlink into it:

1. **`npm ci`** dereferences the symlink, empties the shared directory, and
   exits 0 with only a `warn reify Removing non-directory` — a **silent
   build-time wipe** that poisons every concurrent sibling worktree.

2. **`rm -rf web/node_modules/`** (trailing slash) — and glob forms like
   `web/node_modules/*` — follow the symlink and empty the shared target
   instead of removing only the link.

### The safe way

Run `npm ci` directly inside the worktree's `web/` directory. npm reads
`package-lock.json` for exact reproducibility and the shared `~/.npm/`
on-disk cache makes it fast on repeat runs.

```bash
# Inside the worktree:
cd web && npm ci
```

Or use the convenience helper:

```bash
./web/scripts/setup-worktree.sh
```

No symlinks. No shared state. Each worktree owns its `web/node_modules/`
completely — cleanup in one worktree can never affect another.

### How to verify your setup is safe

Run the isolation guard test:

```bash
./web/scripts/test-worktree-isolation.sh
```

It asserts that `web/node_modules` is a real directory (not a symlink) and
is independent of any shared checkout.
