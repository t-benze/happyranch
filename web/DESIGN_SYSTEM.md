# DESIGN_SYSTEM.md — OPC Founder Console

**Status:** v0.1 — implementation contract. Supersedes the deleted `SHADCN_ADOPTION.md`.
**Companion to:** `web/DESIGN.md` (token source of truth), `web/UI_SPEC.md` (per-screen UX),
`web/ARCHITECTURE.md` (the three-layer boundary rule this doc reconciles with).

---

## 1. Opening summary

OPC's web UI is a localhost SPA used by exactly one human, every working day, for years.
That posture pushes us toward **Design System as Code**: every visual primitive lives
in the repo as code we own, every screen is composed from a small vocabulary of named
patterns, and an AI designer agent can author a new screen by reading a machine-readable
registry rather than guessing at Tailwind classes.

Concretely:

- **shadcn/ui** is the primitive layer. We copy its sources into the repo
  (`src/design-system/primitives/`), wire its CSS variables to **our** semantic tokens
  from `web/DESIGN.md`, and treat any edit to a primitive as a separate design-system PR.
- **Tailwind v4** owns the token surface via a single `@theme` block in `tokens.css`.
  No more `tailwind.config.ts`. No hex codes outside that file.
- **A `design-system/` folder** replaces today's `web/src/components/`. It splits into
  `tokens / primitives / patterns / layouts / providers` and exposes a generated
  `registry.json` catalogue.
- **A `prototypes/` sandbox** lets the designer agent ship a composition wired to mock
  data via `<PrototypeProvider>`. When the founder approves, the dev agent moves the
  same file into `features/<domain>/` and swaps providers — no re-styling.
- **Lint + CI hold the contract:** no inline `style`, no Tailwind arbitrary values, no
  hex codes outside `tokens.css`, no cross-feature imports.

What changes from today: `src/components/` (Button, Modal, TopBar) is deleted as a
folder; Button is replaced by `design-system/primitives/Button` (shadcn-style with our
token variants), Modal is replaced by shadcn `Dialog`, TopBar moves into
`design-system/layouts/AppShell/`. The `lib/api/` layer is untouched — it stays the
data layer. `features/<domain>/` stays, but its contents shrink to *compositions*
(pages + hooks) once patterns and layouts move out.

---

## 2. How this lands in the OPC worktree

### Reconciliation with `ARCHITECTURE.md`'s three-layer rule

The current rule reads:

1. `src/lib/api/<X>.ts` — daemon route mirror
2. `src/features/<domain>/` — feature folders
3. `src/components/` — generic primitives (promoted on third use)

That rule survives, with one substitution: **`src/components/` is replaced by
`src/design-system/`**, which is itself split into five sub-layers. The boundary
rule extends:

> Features may import only from `@/lib/`, `@/design-system/`, `@/hooks/`, and `@/mocks/`.
> Compositions may **not** import other features. Primitives may not import patterns,
> layouts, or hooks. Patterns may import primitives but not layouts.

### What moves, what stays, what's deleted

| File / folder today | Fate | Lands at |
|---|---|---|
| `src/components/Button.tsx` | **deleted** | replaced by `src/design-system/primitives/Button.tsx` (shadcn-style) |
| `src/components/Modal.tsx` | **deleted** | replaced by `src/design-system/primitives/Dialog.tsx` (shadcn Radix Dialog) |
| `src/components/TopBar.tsx` | **moved** | `src/design-system/layouts/AppShell/TopBar.tsx` |
| `src/features/threads/InboxRow.tsx` | **moved + slimmed** | `src/design-system/patterns/InboxRow.tsx` (data-shape-driven, no hook calls) |
| `src/features/threads/MessageBubble.tsx` | **moved** | `src/design-system/patterns/MessageBubble.tsx` |
| `src/features/threads/{New,Invite,Archive,Abandon,Extend}Dialog.tsx` | **kept** | features stay; they import `Dialog` primitive + `FormField` pattern |
| `src/features/threads/ThreadsPage.tsx` | **stays as composition** | imports layout + patterns only |
| `src/features/threads/hooks.ts` | **split** | data hooks stay (renamed `useThreadsListReal`, `…Real`); provider-aware wrappers live in `src/hooks/threads.ts` |
| `src/lib/api/*` | **unchanged** | data layer remains 1:1 with daemon routes |
| `tailwind.config.ts` | **deleted** | replaced by `@theme` block in `src/design-system/tokens/tokens.css` |
| `src/styles.css` | **rewritten** | becomes a 3-line file that `@import`s tokens + tailwind |

### New tree

```
web/
├─ index.html
├─ vite.config.ts
├─ package.json
├─ components.json                      # shadcn config
├─ eslint.config.js                     # flat config, import rules
├─ scripts/
│  └─ build-registry.ts                 # walks design-system/, writes registry.json
├─ src/
│  ├─ main.tsx
│  ├─ App.tsx
│  ├─ routes.tsx
│  ├─ styles.css                        # 3 lines: @import tokens; @import "tailwindcss";
│  ├─ design-system/
│  │  ├─ tokens/
│  │  │  ├─ tokens.css                  # @theme — single source of truth
│  │  │  └─ tokens.json                 # optional W3C export
│  │  ├─ primitives/                    # shadcn copy-in. DO NOT EDIT AD HOC.
│  │  │  ├─ Button.tsx
│  │  │  ├─ Dialog.tsx
│  │  │  ├─ DropdownMenu.tsx
│  │  │  ├─ Tooltip.tsx
│  │  │  ├─ Tabs.tsx
│  │  │  ├─ Input.tsx
│  │  │  ├─ Textarea.tsx
│  │  │  └─ Label.tsx
│  │  ├─ patterns/                      # OUR composites
│  │  │  ├─ AgentChip.tsx
│  │  │  ├─ IdBadge.tsx
│  │  │  ├─ KbdChip.tsx
│  │  │  ├─ StatusBadge.tsx
│  │  │  ├─ TierBadge.tsx
│  │  │  ├─ InboxRow.tsx
│  │  │  ├─ MessageBubble.tsx
│  │  │  ├─ Composer.tsx
│  │  │  ├─ PageHeader.tsx
│  │  │  ├─ EmptyState.tsx
│  │  │  ├─ FormField.tsx               # Label + Input/Textarea + error
│  │  │  └─ HelpSheet.tsx               # the help-as-dialog
│  │  ├─ layouts/                       # slot-based
│  │  │  ├─ AppShell/
│  │  │  │  ├─ AppShell.tsx             # TopBar + body slot + Statusbar
│  │  │  │  ├─ TopBar.tsx
│  │  │  │  └─ Statusbar.tsx
│  │  │  ├─ ThreadsLayout.tsx           # 340px + 1fr
│  │  │  └─ DashboardLayout.tsx         # 240px + 1fr (tasks/audit/kb)
│  │  ├─ providers/
│  │  │  ├─ AppProvider.tsx             # real-data hooks; wraps QueryClient + auth
│  │  │  ├─ PrototypeProvider.tsx       # mock-data hooks
│  │  │  └─ DataContext.ts              # the shared hook registry
│  │  ├─ index.ts                       # public barrel — features import from here
│  │  └─ registry.json                  # GENERATED — do not hand-edit
│  ├─ prototypes/                       # designer-agent sandbox
│  │  ├─ index.tsx                      # /__prototypes route
│  │  └─ threads-v2/
│  │     └─ screen.tsx                  # uses <PrototypeProvider>
│  ├─ mocks/
│  │  ├─ threads.ts                     # canned ThreadRecord[]
│  │  ├─ messages.ts
│  │  └─ index.ts
│  ├─ hooks/                            # provider-aware data hooks
│  │  ├─ threads.ts                     # useThreadsList → resolves to real OR mock
│  │  └─ index.ts
│  ├─ lib/                              # UNCHANGED — data layer
│  │  └─ api/...
│  ├─ features/                         # production compositions
│  │  └─ threads/
│  │     ├─ ThreadsPage.tsx
│  │     ├─ ThreadDetailPane.tsx
│  │     ├─ {New,Invite,Archive,Abandon,Extend}Dialog.tsx
│  │     └─ strings.ts
│  └─ test/
│     ├─ openapi-coverage.test.ts        # UNCHANGED
│     └─ design-system-contract.test.ts  # NEW — asserts registry freshness
```

---

## 3. Token migration plan

`DESIGN.md` is the source of truth for token *values* (palette + semantic). This
section maps every semantic token in DESIGN.md to a Tailwind v4 `@theme` CSS variable.
The naming rule is mechanical:

> `{family}.{slot}.{role}` → `--{family}-{slot}-{role}` (dots and underscores → dashes).

So `colors.semantic.dark.surface.canvas` becomes `--color-surface-canvas` (the
`semantic.dark` prefix collapses — there is no `--color-semantic-dark-surface-canvas`,
because dark is the default and light is a `[data-theme="light"]` override).

### Mapping reference (DESIGN.md → tokens.css variable)

| DESIGN.md token path | CSS variable | Tailwind utility (v4 auto-derives) |
|---|---|---|
| `surface.canvas` | `--color-surface-canvas` | `bg-surface-canvas` |
| `surface.sunken` | `--color-surface-sunken` | `bg-surface-sunken` |
| `surface.raised` | `--color-surface-raised` | `bg-surface-raised` |
| `surface.overlay` | `--color-surface-overlay` | `bg-surface-overlay` |
| `surface.scrim` | `--color-surface-scrim` | `bg-surface-scrim` |
| `text.primary` | `--color-text-primary` | `text-text-primary` |
| `text.secondary` | `--color-text-secondary` | `text-text-secondary` |
| `text.muted` | `--color-text-muted` | `text-text-muted` |
| `text.inverse` | `--color-text-inverse` | `text-text-inverse` |
| `border.subtle` | `--color-border-subtle` | `border-border-subtle` |
| `border.default` | `--color-border-default` | `border-border-default` |
| `border.strong` | `--color-border-strong` | `border-border-strong` |
| `accent.default` | `--color-accent-default` | `bg-accent-default` |
| `accent.hover` | `--color-accent-hover` | `bg-accent-hover` |
| `accent.muted` | `--color-accent-muted` | `bg-accent-muted` |
| `accent.ring` | `--color-accent-ring` | `ring-accent-ring` |
| `tier.green` / `.green_tint` | `--color-tier-green` / `--color-tier-green-tint` | `text-tier-green` / `bg-tier-green-tint` |
| `tier.yellow` / `.yellow_tint` | `--color-tier-yellow` / `--color-tier-yellow-tint` | … |
| `tier.red` / `.red_tint` | `--color-tier-red` / `--color-tier-red-tint` | … |
| `agent.manager` / `.worker` / `.founder` | `--color-agent-manager` etc. | `bg-agent-manager` |
| `status.open` / `archiving` / `archived` / `abandoned` / `blocked` / `escalated` | `--color-status-*` | `text-status-open` |
| `id.thread` / `id.task` | `--color-id-thread` / `--color-id-task` | `text-id-thread` |
| `feedback.{success,warning,danger,info}` | `--color-feedback-*` | `text-feedback-success` |
| `typography.scale.body` (size+line+weight) | `--text-body` (with line-height + weight via the v4 `--text-body--line-height` convention) | `text-body` |
| `layout.spacing.*` | `--spacing-1` through `--spacing-8` | `p-3`, `gap-5` |
| `layout.radius.*` | `--radius-sm/md/lg/pill` | `rounded-md` |
| `shapes.motion.fast/normal/slow` | `--motion-fast` etc. | use in arbitrary `transition-[var(--motion-fast)]` — no, see §10 |

### Where shadcn slots in

shadcn's components reference a small set of generic semantic vars (`--background`,
`--foreground`, `--primary`, `--card`, `--border`, `--ring`, etc.). We **alias** those
to our semantic tokens, so shadcn primitives inherit our palette without modification:

```css
/* in tokens.css, after the @theme block */
:root {
  --background:        var(--color-surface-canvas);
  --foreground:        var(--color-text-primary);
  --card:              var(--color-surface-raised);
  --card-foreground:   var(--color-text-primary);
  --popover:           var(--color-surface-overlay);
  --popover-foreground:var(--color-text-primary);
  --primary:           var(--color-accent-default);
  --primary-foreground:var(--color-text-inverse);
  --secondary:         var(--color-surface-raised);
  --secondary-foreground: var(--color-text-primary);
  --muted:             var(--color-surface-sunken);
  --muted-foreground:  var(--color-text-muted);
  --border:            var(--color-border-default);
  --input:             var(--color-border-default);
  --ring:              var(--color-accent-ring);
  --destructive:       var(--color-tier-red);
  --destructive-foreground: var(--color-text-inverse);
  --radius:            var(--radius-md);
}
```

### `tokens.css` skeleton (first 15+ tokens, dark + light)

```css
/* src/design-system/tokens/tokens.css
 * Single source of truth for design values. Generated from DESIGN.md.
 * NEVER add a hex code here without a corresponding DESIGN.md entry. */

@theme {
  /* ---- Surface (dark mode default) ---- */
  --color-surface-canvas:  #0c0d0f;   /* {palette.ink_950}  */
  --color-surface-sunken:  #121317;   /* {palette.ink_900}  */
  --color-surface-raised:  #171920;   /* {palette.ink_850}  */
  --color-surface-overlay: #1d1f27;   /* {palette.ink_800}  */
  --color-surface-scrim:   rgba(0, 0, 0, 0.55);

  /* ---- Text ---- */
  --color-text-primary:    #e7e8ec;   /* {palette.ink_100}  */
  --color-text-secondary:  #9aa0ac;   /* {palette.ink_300}  */
  --color-text-muted:      #6d7280;   /* {palette.ink_400}  */
  --color-text-inverse:    #121317;   /* {palette.ink_900}  */

  /* ---- Border ---- */
  --color-border-subtle:   #1d1f27;   /* {palette.ink_800}  */
  --color-border-default:  #262932;   /* {palette.ink_700}  */
  --color-border-strong:   #363a45;   /* {palette.ink_600}  */

  /* ---- Accent ---- */
  --color-accent-default:  #1894d3;   /* {palette.signal_500} */
  --color-accent-hover:    #3aabe3;   /* {palette.signal_400} */
  --color-accent-muted:    rgba(24, 148, 211, 0.12);
  --color-accent-ring:     rgba(24, 148, 211, 0.45);

  /* ---- Tiers (full set elided; same convention) ---- */
  --color-tier-green:       #2ea36a;
  --color-tier-green-tint:  rgba(46, 163, 106, 0.14);
  /* …yellow, red, agent.*, status.*, id.*, feedback.* per DESIGN.md §1 ---- */

  /* ---- Spacing — 8pt grid + 4pt hairline ---- */
  --spacing-0: 0;
  --spacing-1: 0.25rem;
  --spacing-2: 0.5rem;
  --spacing-3: 0.75rem;
  --spacing-4: 1rem;
  --spacing-5: 1.5rem;
  --spacing-6: 2rem;
  --spacing-7: 3rem;
  --spacing-8: 4rem;

  /* ---- Radius ---- */
  --radius-none: 0;
  --radius-sm:   0.1875rem;
  --radius-md:   0.3125rem;
  --radius-lg:   0.5rem;
  --radius-pill: 999px;

  /* ---- Typography families ---- */
  --font-sans: "Public Sans", "Public Sans Local", -apple-system,
               BlinkMacSystemFont, "Segoe UI", Helvetica, sans-serif;
  --font-mono: "JetBrains Mono", "JetBrains Mono Local", ui-monospace,
               "SF Mono", Menlo, Consolas, monospace;

  /* ---- Type scale (size + line + weight, v4 convention) ---- */
  --text-body:                0.875rem;
  --text-body--line-height:   1.375rem;
  --text-body--font-weight:   400;
  --text-body-lg:             0.9375rem;
  --text-body-lg--line-height:1.5rem;
  /* …body_sm, label, overline, caption, mono_md, mono_sm, code, h1-h3, display */

  /* ---- Motion ---- */
  --motion-fast:   120ms cubic-bezier(0.2, 0, 0, 1);
  --motion-normal: 200ms cubic-bezier(0.2, 0, 0, 1);
  --motion-slow:   320ms cubic-bezier(0.2, 0, 0, 1);
}

/* ---- Light-mode overrides ---- */
[data-theme="light"] {
  --color-surface-canvas:  #fdfdfb;   /* {palette.paper}    */
  --color-surface-sunken:  #f3f4f6;   /* {palette.ink_050}  */
  --color-surface-raised:  #ffffff;
  --color-surface-overlay: #ffffff;
  --color-surface-scrim:   rgba(12, 13, 15, 0.35);

  --color-text-primary:    #121317;
  --color-text-secondary:  #363a45;
  --color-text-muted:      #4c515e;
  --color-text-inverse:    #fafbfc;

  --color-border-subtle:   #e7e8ec;
  --color-border-default:  #c4c8d0;
  --color-border-strong:   #9aa0ac;

  --color-accent-default:  #1078b0;   /* {palette.signal_600} */
  --color-accent-hover:    #0e5a86;
  --color-accent-muted:    rgba(16, 120, 176, 0.10);
  --color-accent-ring:     rgba(16, 120, 176, 0.35);

  /* …tier/status/agent overrides per DESIGN.md `light` block */
}

/* ---- prefers-color-scheme respect ----
 * Default is dark, but if the user has NOT explicitly chosen a theme AND the
 * OS reports light, mirror the light overrides. The theme toggle writes
 * data-theme="dark|light" on <html>, which wins over this block. */
@media (prefers-color-scheme: light) {
  :root:not([data-theme]) {
    --color-surface-canvas:  #fdfdfb;
    /* …same body as the [data-theme="light"] block; expand at PR-1 time */
  }
}
```

### Done definition for tokens

- Every key under `colors.semantic.dark` in DESIGN.md has a `--color-*` variable.
- No hex code exists anywhere in `src/` outside `tokens.css`.
- Removing `tailwind.config.ts` does not change pixel output (verified by
  visual diff on the threads page).

---

## 4. Tailwind 3 → 4 migration

Tailwind v4 is **CSS-first**: there is no `tailwind.config.ts`, the `@theme` block in
CSS owns the design vocabulary, and utility class generation reads from those variables.
This is exactly what we want — one file, one source.

### What changes

| v3 (today) | v4 (target) |
|---|---|
| `@tailwind base; @tailwind components; @tailwind utilities;` | `@import "tailwindcss";` |
| `tailwind.config.ts` defines `theme.extend.colors.{bg,fg,accent,tier}` | `@theme { --color-* }` in `tokens.css` |
| `postcss.config.cjs` runs `tailwindcss` + `autoprefixer` plugins | `@tailwindcss/vite` plugin in `vite.config.ts`; PostCSS gone |
| Custom utility `.input` in `@layer components` | shadcn `Input` primitive replaces it; `@layer components` survives but only for `.prose` overrides |
| Class names: `bg-bg`, `bg-bg-raised`, `text-fg-muted` | Renamed: `bg-surface-canvas`, `bg-surface-raised`, `text-text-muted` |
| `eslint-plugin-tailwindcss` v3 | v4-compatible release (or `prettier-plugin-tailwindcss` + a local hex-check) |

### Class-rename compat path

The current `tailwind.config.ts` uses `bg`, `bg-subtle`, `bg-raised`, `fg`, `fg-muted`,
`accent`, `tier-green/yellow/red` — short, ergonomic names that **don't match
DESIGN.md's semantic names**. We will rename them as part of PR 1 (it's a sed-able
mechanical pass: `bg-bg-raised` → `bg-surface-raised`, `text-fg` → `text-text-primary`,
`text-fg-muted` → `text-text-muted`, `bg-accent` → `bg-accent-default`, etc.). The
`accent-DEFAULT/hover` shape collapses to `accent-default/accent-hover` to match
DESIGN.md exactly.

### Risks

1. **shadcn templates assume v3 names** in some docs. We handle this by hand-mapping
   shadcn's CSS vars to ours in §3, so primitives don't carry v3 idioms.
2. **`eslint-plugin-tailwindcss` v4 support** has been unstable in early 2026. If the
   v4 plugin still chokes on `@theme` at PR-1 time, we drop the plugin and run a
   simpler `verify-design-system.sh` grep for `\[#[0-9a-f]{3,8}\]` and `\bp-\[`
   (arbitrary value markers). Cheaper, ours-to-own.
3. **JIT differences:** v4 generates classes on-demand from `@theme` keys. If a
   class doesn't appear in source, it doesn't exist at runtime. Mitigation: the
   registry generator (see §9) scans patterns and primitives so the variant set
   is statically discoverable.

### Rollback

PR 1 is the only PR that flips Tailwind major. Rollback path: revert that PR — all
downstream PRs branch from a tag at v4-known-good and merge forward only. We do not
half-migrate.

---

## 5. shadcn initialization for this worktree

### Commands (run once, in PR 1)

```bash
# In web/
npx shadcn@latest init \
  --base-color neutral \
  --css-variables \
  --no-rsc \
  --typescript

# Then install the v0.1 primitive set, one by one:
npx shadcn@latest add button
npx shadcn@latest add dialog
npx shadcn@latest add dropdown-menu
npx shadcn@latest add tooltip
npx shadcn@latest add tabs
npx shadcn@latest add input
npx shadcn@latest add textarea
npx shadcn@latest add label
```

We do **not** install `card`, `accordion`, `popover`, `command`, `sheet`, `toast`,
`avatar`, `select` in v0.1. Justification per UI_SPEC reference:

| Primitive | v0.1? | Why |
|---|---|---|
| `Button` | yes | every screen; replaces `components/Button.tsx` |
| `Dialog` | yes | the five Threads dialogs (UI_SPEC §4); replaces `components/Modal.tsx` |
| `DropdownMenu` | yes | OrgSwitcher in TopBar (UI_SPEC §1) |
| `Tooltip` | yes | disabled nav tabs "Coming soon" (UI_SPEC §1.States) |
| `Tabs` | yes | inbox status tabs (UI_SPEC §2) |
| `Input` / `Textarea` / `Label` | yes | composer + dialog forms |
| `Sheet` (side drawer) | **deferred** | HelpDrawer renders as a `Dialog` per UI_SPEC §5 |
| `Command` (cmd-K) | **deferred** | UI_SPEC §12 flags it as future |
| `Toast` | **deferred** | we'll roll a 30-line custom queue or pull `sonner`; decide in PR 5 |
| `Select` (Radix) | **deferred** | native `<select>` per DESIGN.md `components.select` |
| `Popover` | **deferred** | build-version popover (Statusbar) can use `DropdownMenu` |

### `components.json`

```jsonc
{
  "$schema": "https://ui.shadcn.com/schema.json",
  "style": "default",
  "rsc": false,
  "tsx": true,
  "tailwind": {
    "config": "",
    "css": "src/design-system/tokens/tokens.css",
    "baseColor": "neutral",
    "cssVariables": true,
    "prefix": ""
  },
  "aliases": {
    "components": "@/design-system/primitives",
    "utils": "@/design-system/primitives/utils",
    "ui": "@/design-system/primitives",
    "lib": "@/lib",
    "hooks": "@/hooks"
  },
  "iconLibrary": "lucide"
}
```

Note: `"config": ""` is the v4 signal that there's no JS config; shadcn reads tokens
from the CSS at `css` path.

---

## 6. The five layers, adapted

### Layer 1 — Tokens

- **What we have today:** `tailwind.config.ts` with ~12 colors and a system-ui font stack.
- **What it becomes:** `src/design-system/tokens/tokens.css` — every value in DESIGN.md
  expressed as a CSS custom property inside `@theme`. Tailwind v4 derives utilities;
  shadcn primitives consume the aliased generic vars.
- **Import rule:** Nobody imports tokens directly. Components reference them via
  Tailwind utilities (`bg-surface-canvas`) or via the aliased shadcn vars (`bg-card`).
- **Worked example:** `tier.red` lives once, in `tokens.css` as `--color-tier-red`.
  The `TierBadge` pattern uses `bg-tier-red-tint text-tier-red`. The shadcn `Button`
  variant `destructive` uses `bg-destructive` which aliases to `--color-tier-red`.
  Change the hex once; every consumer updates.

### Layer 2 — Primitives (shadcn)

- **What we have today:** `components/Button.tsx` (hand-rolled, three variants),
  `components/Modal.tsx` (Radix-less hand-rolled).
- **What it becomes:** `src/design-system/primitives/` — shadcn source, copied in.
  Each primitive is a TS file we own. The contract: **edits to these files are
  design-system PRs, not feature PRs.** A feature that wants a new variant proposes
  it; the design-system PR adds it; the feature consumes it.
- **Import rule:** Primitives import only from `@/design-system/primitives/utils`
  (the `cn` helper) and React. No patterns, no hooks, no `lib/api`.
- **Worked example:** `Button.tsx` ships shadcn's CVA-based variant block. We extend
  the variant union to match DESIGN.md `components.button.variants`:
  `primary | secondary | ghost | danger | destructive_filled` (shadcn ships
  `default | destructive | outline | secondary | ghost | link` — we rename to
  match our doc and drop `link`). The map:

  | DESIGN.md variant | shadcn default | Our resolution |
  |---|---|---|
  | `primary` | `default` | rename CVA key to `primary` |
  | `secondary` | `secondary` | identical |
  | `ghost` | `ghost` | identical |
  | `danger` | (none) | new variant: `text-tier-red hover:bg-tier-red-tint`, no fill |
  | `destructive_filled` | `destructive` | rename CVA key |

### Layer 3 — Patterns

- **What we have today:** `features/threads/{InboxRow,MessageBubble,ThreadHeader}.tsx` —
  product-specific composites bolted to TanStack Query hooks.
- **What it becomes:** `src/design-system/patterns/` — same composites, but
  **data-shape-driven** (props in, JSX out). No hook calls. No `lib/api` imports.
  A pattern is a pure function of its props.
- **Import rule:** Patterns import primitives and other patterns. They MAY NOT import
  layouts, providers, hooks, or `lib/api`.
- **Worked example:** `InboxRow` today reads `useThread()` indirectly. New version:

  ```tsx
  // src/design-system/patterns/InboxRow.tsx
  type InboxRowProps = {
    threadId: string;            // "THR-0123"
    subject: string;
    lastSpeaker: { name: string; role: "manager" | "worker" | "founder" };
    ageRelative: string;
    status: "open" | "archiving" | "archived" | "abandoned";
    needsYou: boolean;
    active: boolean;
    onSelect: () => void;
  };
  export function InboxRow(props: InboxRowProps) { /* token-only utilities */ }
  ```

  Consumes DESIGN.md `components.inbox_row` block.

### Layer 4 — Layouts

- **What we have today:** A `flex h-full flex-col` shell in `App.tsx` and ad-hoc
  grids in `ThreadsPage.tsx`.
- **What it becomes:** `src/design-system/layouts/` — slot-based templates. Designer
  picks a layout, fills slots.
- **Import rule:** Layouts import primitives + patterns. They are the only place
  CSS `grid-template-columns` / `grid-template-rows` lives at the app-shell scale.
- **Worked examples:**
  - `AppShell` — `<header slot/> <main slot/> <footer slot/>`, 48px / 1fr / 24px.
  - `ThreadsLayout` — `<inbox slot/> <detail slot/>`, 340px / 1fr.
  - `DashboardLayout` — `<sidebar slot/> <canvas slot/>`, 240px / 1fr (Tasks/Audit/KB).

### Layer 5 — Compositions

- **What we have today:** `features/threads/ThreadsPage.tsx` — does everything: layout,
  data, patterns, dialogs.
- **What it becomes:** A composition is a thin file that:
  1. Picks a layout.
  2. Fills slots with patterns.
  3. Uses provider-aware hooks from `@/hooks/` (which delegate to real or mock data).
  4. Owns dialog state and routing.
- **Import rule:** Compositions import from `@/design-system/`, `@/hooks/`, and
  `@/lib/` (only for types, not for `fetch`). They do NOT call `fetch` directly.
- **Worked example:** Approved `ThreadsPage.tsx` renders identically in `prototypes/`
  (wrapped in `<PrototypeProvider>`) and in `features/threads/` (wrapped in
  `<AppProvider>`). The dev PR moves the file and adds nothing more.

---

## 7. Pattern catalogue for v0.1 (threads feature only)

Every pattern needed to ship the threads feature through layers 1–5. One row per
pattern, mapped from UI_SPEC molecules and DESIGN.md `components.*` blocks.

| Pattern | One-line | Consumes DESIGN.md block |
|---|---|---|
| `PageHeader` | Title + meta line + right-aligned action row. Used by `ThreadHeader`. | `typography.h2`, `typography.caption` |
| `EmptyState` | Icon + title + body + optional CTA, centered, 28rem max. | `components.empty_state` |
| `AgentChip` | 6px role dot + name. The only place agent identity is rendered. | `components.agent_chip` |
| `IdBadge` | Monospace `THR-NNN` / `TASK-NNN`, color-tinted, no fill. | `components.badge.variants.id_*` |
| `KbdChip` | Keycap with inset bottom shadow. Mono 11px. | `components.kbd_chip` |
| `StatusBadge` | open / archiving / archived / abandoned / blocked / escalated. | `components.badge.variants.status_*` |
| `TierBadge` | green / yellow / red tier scorecard pill. Reserved for Agents page. | `components.badge.variants.tier_*` |
| `MessageBubble` | One of `{founder, worker, manager, decline, system}` variants. | `components.message_bubble` |
| `InboxRow` | Two-line row, needs-you dot, active marker. | `components.inbox_row` |
| `Composer` | Sticky-bottom textarea + helper + Send. | `components.textarea` + `components.button.primary` |
| `FormField` | Label + Input/Textarea + inline error message. Used by all five dialogs. | `components.input` + `typography.label` |
| `HelpSheet` | The HelpDrawer rendered as a Dialog (per UI_SPEC §5). | `components.dialog` + `components.kbd_chip` |

**Future patterns (not in v0.1):** `TaskCard` (Tasks page), `ScorecardRow` (Agents
page), `AuditRow` (Audit page), `KbCard` (KB page). Each unlocks one placeholder
screen from UI_SPEC §§8–11.

---

## 8. Prototype harness for OPC

### Concept

Two providers, identical hook surface, different implementations. The same composition
file renders against mock data in `prototypes/` and against real data in `features/`.

### `DataContext` shape

```tsx
// src/design-system/providers/DataContext.ts
import { createContext, useContext } from "react";
import type {
  ThreadDetailResponse, ThreadInboxEvent, ThreadMessage, ThreadRecord,
} from "@/lib/api/types";

export type ThreadsApi = {
  useThreadsList: (params?: { status?: string }) => {
    data?: { threads: ThreadRecord[] }; isLoading: boolean; error?: Error;
  };
  useThread: (threadId?: string) => {
    data?: ThreadDetailResponse; isLoading: boolean; error?: Error;
  };
  useThreadMessages: (threadId?: string) => {
    data?: { messages: ThreadMessage[] }; isLoading: boolean;
  };
  useThreadsInboxSSE: () => void;       // no-op in mock
  // Mutations follow the same useFooMutation() shape.
};

export const DataContext = createContext<{ threads: ThreadsApi } | null>(null);
export function useData() {
  const ctx = useContext(DataContext);
  if (!ctx) throw new Error("useData must be inside <AppProvider> or <PrototypeProvider>");
  return ctx;
}
```

### `AppProvider` (real)

Wraps QueryClient + auth bootstrap, then implements `ThreadsApi` by delegating to
the existing TanStack Query hooks in `features/threads/hooks.ts` (which become
internal — moved into `design-system/providers/_real-threads.ts`).

### `PrototypeProvider` (mock)

Implements `ThreadsApi` by returning canned fixtures from `src/mocks/` with
`setTimeout`-delayed promises for loading-state realism. Mutations log to console
and `setQueryData` against an in-memory store — refresh-resets are intentional.

### Hook reorganization

Today the threads feature ships four read hooks (`useThreadsList`, `useThread`,
`useThreadMessages`, `useThreadsInboxSSE`) and seven write hooks in
`features/threads/hooks.ts`. New layout:

- **`src/design-system/providers/_real-threads.ts`** — keeps the existing
  TanStack-Query bodies. Private to the provider.
- **`src/design-system/providers/_mock-threads.ts`** — mock implementations.
- **`src/hooks/threads.ts`** — the public surface; every hook reads `useData()`
  and forwards. Compositions import from `@/hooks/threads`, never directly from
  the providers.

This keeps the boundary rule from §2 intact: compositions never know whether they
got real or mock data.

### `src/mocks/` layout

```
src/mocks/
├─ index.ts                # barrel
├─ threads.ts              # const MOCK_THREADS: ThreadRecord[]
├─ messages.ts             # const MOCK_MESSAGES: Record<string, ThreadMessage[]>
├─ agents.ts               # for Agents page once we ship it
└─ orgs.ts                 # for the OrgSwitcher demo
```

Fixtures are stable across hot-reload (no `Math.random()`) so visual diffs are
deterministic.

### Sandbox route

`src/prototypes/index.tsx` defines a `/__prototypes` route (excluded from the main
nav, but reachable by typing the URL). Each prototype is a sub-route:
`/__prototypes/threads-v2`. The route is unmounted in production builds via a
`if (import.meta.env.PROD && !import.meta.env.VITE_ENABLE_PROTOTYPES) return null`
guard — kept lazy, kept cheap.

---

## 9. Registry

### Recommendation: handwritten manifest + cheap generator

`react-docgen-typescript` works but is heavy (drags in the TS compiler at build
time, slow on a localhost SPA we rebuild often). Given we already ship a Zod
manifest at `web/UI_SPEC.components.ts` (§13 of UI_SPEC.md), the right answer is:

> Generate `registry.json` from filesystem + a small per-component `meta` export,
> not from TS AST introspection.

Each pattern and primitive declares its metadata inline:

```tsx
// src/design-system/patterns/AgentChip.tsx
export const meta = {
  name: "AgentChip",
  layer: "pattern",
  import: "@/design-system/patterns/AgentChip",
  variants: { role: ["manager", "worker", "founder"] },
  consumes: ["components.agent_chip"],   // DESIGN.md block
  example: "<AgentChip name='content_writer' role='worker' />",
} as const;
```

### Generator script

`scripts/build-registry.ts`:

```ts
import { globSync } from "node:fs";
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const ROOT = "src/design-system";
const FILES = globSync(`${ROOT}/{primitives,patterns,layouts}/**/*.tsx`);

const entries = FILES.map((path) => {
  const src = readFileSync(path, "utf8");
  const match = src.match(/export const meta\s*=\s*({[\s\S]*?})\s*as const;/);
  if (!match) return null;
  // Deliberately a literal eval (the file is ours, the format is constrained).
  // Alternative: a TS-to-JSON pass via `tsc --module commonjs` on the meta file.
  return Function(`"use strict"; return (${match[1]})`)();
}).filter(Boolean);

writeFileSync(
  join(ROOT, "registry.json"),
  JSON.stringify(
    { generatedAt: new Date().toISOString(), components: entries },
    null, 2
  )
);
```

Hooked into `package.json`:

```jsonc
{
  "scripts": {
    "build:registry": "tsx scripts/build-registry.ts",
    "prebuild": "npm run build:registry",
    "predev":   "npm run build:registry"
  }
}
```

### Fields in `registry.json`

```jsonc
{
  "generatedAt": "2026-05-15T12:00:00Z",
  "components": [
    {
      "name": "AgentChip",
      "layer": "pattern",
      "import": "@/design-system/patterns/AgentChip",
      "variants": { "role": ["manager", "worker", "founder"] },
      "consumes": ["components.agent_chip"],
      "example": "<AgentChip name='content_writer' role='worker' />"
    }
    /* … */
  ]
}
```

### How agents consume it

**Decision: a committed local JSON file. No daemon route, no MCP sidecar.**

`web/src/design-system/registry.json` is regenerated by `npm run build:registry`
(wired into `prebuild` + `predev`) and **committed to the repo**. CI fails if it
goes stale (see §10).

The designer agent reads it directly off disk with its standard `Read` tool —
the file lives in the same worktree the agent is already operating inside. The
dev agent reads it the same way. No transport, no auth, no extra moving parts.

Tradeoff accepted: the file is a build artifact in version control. We pay one
commit-noise tax per primitive/pattern change in exchange for the registry being
available to any agent or human with read access to the repo, with zero
infrastructure. The stale-check in CI keeps it honest.

We revisit a daemon route or a dedicated MCP server only if (a) registries grow
beyond components to include hooks and routes, or (b) we ship a multi-agent
design workflow where registry access has to be brokered.

---

## 10. Enforcement

### ESLint flat config

```js
// eslint.config.js — replaces .eslintrc
import tseslint from "typescript-eslint";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";
import tailwind from "eslint-plugin-tailwindcss";

export default tseslint.config(
  { ignores: ["dist", "node_modules", "src/design-system/registry.json"] },

  // Base TS + React
  {
    files: ["src/**/*.{ts,tsx}"],
    extends: [
      ...tseslint.configs.recommendedTypeChecked,
      react.configs.recommended,
      reactHooks.configs.recommended,
    ],
    languageOptions: {
      parserOptions: { project: "./tsconfig.json" },
    },
    rules: {
      "@typescript-eslint/no-explicit-any": "error",
      "react/forbid-component-props": ["error", { forbid: ["style"] }],
      "react/forbid-dom-props":       ["error", { forbid: ["style"] }],
    },
  },

  // No-cross-feature-imports + features may only import sanctioned roots
  {
    files: ["src/features/**/*.{ts,tsx}", "src/prototypes/**/*.{ts,tsx}"],
    rules: {
      "no-restricted-imports": ["error", {
        patterns: [
          { group: ["@/features/*/*"], message:
            "Cross-feature imports are forbidden. Share through @/design-system/ or @/hooks/." },
          { group: ["@/components/*"], message:
            "@/components is deleted. Use @/design-system/primitives or /patterns." },
          { group: ["**/lib/api/*"], message:
            "Compositions must use hooks from @/hooks/, not call lib/api directly." },
        ],
      }],
    },
  },

  // Primitives may not import patterns/layouts/hooks
  {
    files: ["src/design-system/primitives/**/*.{ts,tsx}"],
    rules: {
      "no-restricted-imports": ["error", {
        patterns: [
          { group: ["@/design-system/patterns/*"], message: "primitives may not import patterns" },
          { group: ["@/design-system/layouts/*"],  message: "primitives may not import layouts" },
          { group: ["@/hooks/*", "@/lib/*"],       message: "primitives are pure UI" },
        ],
      }],
    },
  },

  // Tailwind class hygiene
  {
    files: ["src/**/*.{ts,tsx}"],
    plugins: { tailwindcss: tailwind },
    rules: {
      "tailwindcss/no-arbitrary-value":   "error",   // forbids p-[13px], bg-[#fff]
      "tailwindcss/no-contradicting-classname": "error",
      "tailwindcss/classnames-order":     "warn",
    },
  },
);
```

### Hex-code grep (CI)

```bash
# scripts/verify-design-system.sh
#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> ESLint"
npm run lint

echo "==> TypeScript strict"
npm run typecheck

echo "==> Hex codes outside tokens.css"
HEX_HITS=$(
  grep -RIn --include='*.ts' --include='*.tsx' --include='*.css' \
       -E '#[0-9a-fA-F]{3,8}\b' src/ \
    | grep -v 'src/design-system/tokens/tokens.css' \
    || true
)
if [ -n "$HEX_HITS" ]; then
  echo "FAIL: hex codes found outside tokens.css:"
  echo "$HEX_HITS"
  exit 1
fi

echo "==> Registry is fresh"
npm run build:registry
if ! git diff --quiet -- src/design-system/registry.json; then
  echo "FAIL: src/design-system/registry.json is stale. Run npm run build:registry and commit."
  exit 1
fi

echo "OK"
```

### Blocking vs warning

| Check | CI status | Why |
|---|---|---|
| `no-restricted-imports` (boundary rules) | **block** | the entire system depends on this |
| `react/forbid-component-props`/`forbid-dom-props` no `style` | **block** | inline style escapes tokens |
| `tailwindcss/no-arbitrary-value` | **block** | arbitrary values escape tokens |
| `@typescript-eslint/no-explicit-any` | **block** | typed system or it's a guess |
| `tailwindcss/classnames-order` | warn | cosmetic; auto-fixable |
| Hex-code grep | **block** | the load-bearing check; one line of grep does what 200 lines of plugin config almost-do |
| Registry freshness | **block** | otherwise the designer agent reads stale shapes |

---

## 11. Storybook decision

**Recommendation: skip Storybook. Ship a `/__design__` route in the SPA itself.**

Reasoning:

1. **One human user, no design team.** Storybook earns its keep when designers,
   PMs, and engineers all need a stable URL. Here the founder *is* the designer
   *is* the engineer, and the SPA is the design surface.
2. **Localhost-only product.** The SPA is already a Vite dev server; spinning up
   a second Vite/webpack instance for Storybook doubles the build surface for
   zero new capability.
3. **Visual regression is cheaper without Storybook.** Playwright (already in our
   skill list) can snapshot the `/__design__` route directly. No Chromatic, no
   second hosting concern.
4. **A `/__design__` route is one composition that imports `registry.json` and
   renders every entry.** ~80 lines of code. The agent designing a new pattern
   sees it appear in `/__design__` on the next refresh.

Concrete plan: `src/design-system/__design__/index.tsx` reads `registry.json`,
iterates components, renders each example string with `JSON.parse`-safe props.
Same `prefers-color-scheme` + `data-theme` machinery so we visually test light
mode here before we ship it everywhere else.

If a year from now we have three people working on the design system, we revisit.

---

## 12. Migration roadmap

### PR 1 — Foundation flip

- **Scope:** Tailwind v3 → v4. Delete `tailwind.config.ts`, `postcss.config.cjs`,
  rewrite `src/styles.css` → `@import "./design-system/tokens/tokens.css"; @import "tailwindcss";`.
  Add `tokens.css` with the full DESIGN.md semantic set. Run `npx shadcn init` and
  add `Button` + `Dialog`. Replace `components/Modal.tsx` usage in the five threads
  dialogs with the new `Dialog` primitive (search-and-replace, props are
  near-isomorphic). Replace `components/Button.tsx` with `design-system/primitives/Button.tsx`
  via the variant rename map in §6. Delete `components/`. Move `TopBar.tsx` →
  `design-system/layouts/AppShell/TopBar.tsx`.
- **Files touched:** ~25.
- **Blast radius:** All visible screens render through the new system. Pixel diff
  vs `main` should be near-zero (token values are identical to DESIGN.md hexes,
  and we preserved all existing semantics).
- **Success criteria:** App boots, threads page renders unchanged, ESLint warns
  only on `classnames-order`, all existing tests pass.

### PR 2 — Pattern extraction from threads

- **Scope:** Move `InboxRow`, `MessageBubble`, `ThreadHeader` content into
  `design-system/patterns/` as pure-prop patterns. Add `AgentChip`, `IdBadge`,
  `KbdChip`, `StatusBadge`, `EmptyState`, `Composer`, `PageHeader`, `FormField`,
  `HelpSheet` as new patterns. Update `ThreadsPage.tsx` to consume them.
- **Files touched:** ~15.
- **Blast radius:** Threads-only. Other screens are placeholders.
- **Success criteria:** `ThreadsPage.tsx` has zero hex codes, zero inline styles,
  zero TanStack Query imports below the page level; all dialogs use `FormField`.

### PR 3 — Prototype harness

- **Scope:** Add `DataContext`, `AppProvider`, `PrototypeProvider`. Move real
  hook bodies into `_real-threads.ts`; write `_mock-threads.ts` against
  `src/mocks/`. Add the `/__prototypes` route. Demo: ship one prototype
  (`prototypes/threads-v2/screen.tsx`) that renders the same `ThreadsPage`
  composition under mock data.
- **Files touched:** ~12.
- **Blast radius:** Net-additive. No production-route changes.
- **Success criteria:** Both `/orgs/:slug/threads` and `/__prototypes/threads-v2`
  render the same JSX file with different data.

### PR 4 — Registry + design route

- **Scope:** Add `meta` exports to every primitive and pattern. Write
  `scripts/build-registry.ts` which writes `src/design-system/registry.json`.
  Wire `prebuild` + `predev`. Commit the generated `registry.json`. Add the
  `/__design__` composition that imports `registry.json` and renders every
  entry. No daemon route, no `lib/api/` mirror — agents read the file directly
  from the worktree.
- **Files touched:** ~22 (one-line `meta` block per pattern + the script + the
  design route component + the generated JSON).
- **Blast radius:** Net-additive on the web side; zero changes to `src/daemon/`.
  The CI registry-freshness check turns on here.
- **Success criteria:** `npm run build:registry` produces a sorted JSON document
  at `src/design-system/registry.json`; the file is committed and CI fails if
  the committed copy drifts from the regenerated one; `/__design__` renders
  every component example without errors.

### PR 5 — Remaining primitives + lint enforcement

- **Scope:** Add `DropdownMenu`, `Tooltip`, `Tabs`, `Input`, `Textarea`, `Label`.
  Wire OrgSwitcher (TopBar), Tooltip on disabled nav tabs, Tabs in InboxList.
  Flip ESLint blocking rules from `warn` → `error`. Ship
  `scripts/verify-design-system.sh` and make it the CI gate.
- **Files touched:** ~20.
- **Blast radius:** Threads page gets richer interactions; nothing existing breaks.
- **Success criteria:** `bash scripts/verify-design-system.sh` exits 0 on a
  clean tree; an intentional hex code in any feature fails CI.

### PR 6+ — placeholder screens

- Tasks / KB / Audit / Agents come online one PR each, each composing
  `DashboardLayout` + a thin set of new patterns. The migration is "complete"
  before these start; they exist to validate the system scales.

---

## 13. Risks and open decisions

Founder must confirm before PR 1:

1. **Tailwind v4 timing.** v4 stable shipped in early 2025; ecosystem coverage
   (eslint-plugin-tailwindcss, prettier-plugin-tailwindcss, shadcn templates)
   is still uneven in mid-2026. **Recommendation: ship v4 now.** Our hex-code grep
   covers the gap if a plugin lags. Founder veto path: stay on v3, write the
   `@theme` block inside `tailwind.config.ts`'s `theme.extend` instead. Worse
   ergonomics, same correctness.
2. **shadcn baseline color.** **Recommendation: `--base-color neutral`** with our
   tokens overriding the shadcn vars (per §3 alias block). `neutral` is the
   least-opinionated of shadcn's bases and our DESIGN.md inks are warmer-neutral
   already — no fight.
3. **Storybook decision.** **Recommendation: skip in favor of the in-app
   `/__design__` route** (§11). Founder must agree this is enough; otherwise we
   reverse course and PR 4 grows to wire Ladle (lighter than Storybook).
4. **Registry transport.** **Decision: committed local JSON file at
   `web/src/design-system/registry.json`.** Agents read it off disk. No daemon
   route, no MCP server. CI enforces freshness.
5. **Fate of `components/Button.tsx`.** **Recommendation: delete.** The brief
   states shadcn is the primitive layer; keeping a hand-rolled `Button` next to
   the shadcn one is exactly the design fragmentation this system exists to
   prevent.
6. **Fate of `components/Modal.tsx`.** **Recommendation: delete.** Replaced
   by shadcn `Dialog`. The prop surfaces are similar enough (`open`,
   `onOpenChange`, `title`, `children`) that the five threads dialogs
   migrate in PR 1 mechanically.
7. **Fate of `components/TopBar.tsx`.** **Recommendation: move, don't delete.**
   It's a layout fragment, not a primitive. Lands at
   `src/design-system/layouts/AppShell/TopBar.tsx` in PR 1.
8. **Hooks split timing.** **Recommendation: PR 3.** The four read hooks +
   seven write hooks stay where they are through PR 1 and PR 2; the provider
   re-wire is a clean PR on its own.

---

## 14. Out of scope for v0.1

- **Visual-regression CI infra.** Playwright screenshots of `/__design__` are
  cheap enough to add later; not a blocker for the system to ship.
- **Chromatic** or any hosted regression service. Localhost product.
- **MCP server polish.** HTTP + CLI is enough; revisit when registries multiply.
- **Light-mode rollout.** Tokens ship light + dark, but the theme toggle stays
  dark-only in UI until a dedicated PR audits each pattern in light mode.
- **Density toggle** (UI_SPEC §12). The token block supports it; the toggle UI
  doesn't ship in v0.1. Comfortable is the default and the only mode.
- **Command palette (`Cmd-K`).** UI_SPEC §1 already flags it as future.
- **`Toast` primitive.** Today's threads feature has no toast; we add a queue
  pattern when the first need lands (probably with the disconnected/SSE-dropped
  surfaces in §7).
- **Drawer primitive.** HelpDrawer renders as `Dialog` per UI_SPEC §5. Real
  side-drawer is reserved for the future agent-detail sidecar.

---

## 15. Done definition for the migration

When all of the following are checkboxes ticked, v0.1 is done:

- [ ] `src/design-system/tokens/tokens.css` owns every design value referenced
      in the running app. No hex codes anywhere else in `src/`.
- [ ] `web/tailwind.config.ts` does not exist.
- [ ] `web/src/components/` does not exist.
- [ ] Threads feature renders entirely through layers 1–5: tokens →
      shadcn primitives → patterns → `ThreadsLayout` inside `AppShell` →
      `ThreadsPage` composition.
- [ ] `npm run lint` passes with import restrictions on, no-arbitrary-value on,
      no-explicit-any on.
- [ ] `scripts/verify-design-system.sh` exits 0 on a clean tree.
- [ ] `src/design-system/registry.json` exists, is committed, is regenerated by
      `npm run build:registry`, and matches the `meta` exports in `primitives/`
      + `patterns/` + `layouts/`. CI fails if the committed copy drifts.
- [ ] One prototype run end-to-end: designer agent reads `registry.json`, writes
      `prototypes/<feature>/screen.tsx`, founder reviews running prototype at
      `/__prototypes/<feature>`, dev agent moves the file to
      `features/<feature>/` and swaps `<PrototypeProvider>` for `<AppProvider>`
      — without changing layout or styling.
- [ ] Both `web/DESIGN.md` and `web/UI_SPEC.md` are unchanged by this migration
      (we implemented against them, we didn't rewrite them).
