# Grassland web — architecture notes

Localhost React SPA bundled into the FastAPI daemon. See
`docs/superpowers/specs/2026-05-14-web-ui-design.md` for the full design.

## Three layers (strict)

1. **`src/lib/api/<X>.ts`** — Daemon route mirror. One TS module per
   `src/daemon/routes/<X>.py`. Pure functions over a shared `request()` helper.
   Returns typed objects. No React.
2. **`src/features/<domain>/`** — React feature folders. One folder per CLI
   domain. Owns pages, components, dialogs, and TanStack Query hooks. May
   import only from `src/lib/` and `src/components/`. **No cross-feature
   imports.**
3. **`src/components/`** — Generic primitives only. Promoted from a feature on
   third use.

## Boundary rule

> Every browser-callable daemon route maps 1:1 to one TS function in
> `src/lib/api/`. Features compose those functions through TanStack Query
> hooks. Features may not call `fetch` directly. Cross-feature imports are
> forbidden — share through `src/components/` or `src/lib/`.

## What is intentionally not in here

- Agent-callback endpoints (`/report-completion`, `/manage-agent`,
  `/manage-repo`, `/dispatch`, `/learning add|update|promote`, thread
  `/reply`, `/decline`, `/dispatch`, `/close-out`). Those are agent-subprocess
  only and would be a privilege-escalation if exposed in the browser.
- `--as-founder` impersonation surface for KB deletes. Stays TTY-gated in CLI.
- Multi-user concerns: login screens, account model, RBAC. Localhost only.
