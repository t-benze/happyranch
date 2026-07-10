# THR-061 thread-detail fidelity FIX — R2 REVISE (round-2 code review) evidence matrix

Branch `task/TASK-2550` (PR #381). This matrix closes the two HIGH findings from
the round-2 code review:

- **Finding 1 (density UNMET):** the linked-task chip list still pushed the
  `THIS THREAD / Fresh tokens` panel below the fold on the real busy thread.
- **Finding 2 (evidence matrix incomplete):** the R1 set lacked dark before and
  a per-delta design/before/after matrix in both themes.

## How this was rendered (real data — honesty fence)

- **AFTER** = worktree `task/TASK-2550` prod build (R1 fixes + the R2 density cap).
- **BEFORE** = `origin/main` @ `7e09563b` prod build (the deployed state the founder
  named the gaps on).
- Both served through the MEM-003 daemon-proxy → real daemon on `:8765`.
- **`live`** = the real **THR-061** thread — **42 real linked tasks**, real
  participants, real Fresh-tokens rollup (`input_tokens` = 7,591,849 → `7.6M`).
  The `/tokens` intercept fetches the REAL daemon rollup and only *merges* a
  synthetic `THR-DEMO` row, so live THR-061 keeps its true figure (no regression).
- **`demo`** = a synthetic **THR-DEMO** thread that deterministically exercises the
  transient in-flight `replying…` + inline `Abort reply` state (a WORKING responder
  ~6s old) and the design's Fresh-tokens `715K`, so the composer / replying /
  abort deltas are reproducible in a still screenshot.
- Viewport 1440×900. Dark via `localStorage happyranch.theme=dark` + reload.

The **design authority** is `design-target.png` (the founder's a-thread-detail
target). It is a **light-theme** artifact — there is no dark-theme design asset;
dark AFTER is verified against the same target's structure + the design-token dark
palette. Design chip words (`approval`/`running`, both green) are **illustrative**;
the real render shows REAL status via design tokens (e.g. `escalated`→red) — honesty
over paraphrase.

## Finding 1 proof (measured, live THR-061, DOM `getBoundingClientRect`)

| state | composer rows | Fresh-tokens `top` | above fold? | chip `<ul>` height | chips rendered |
|---|---|---|---|---|---|
| BEFORE (origin/main) | 3 (bulky) | **1477px** | **NO** | 1116px (unbounded) | 42 |
| AFTER (R2) | 1 (pill) | **457px** | **YES** | **96px (capped, internal scroll)** | **42 (all, scroll-reachable)** |

The chip list is capped to `max-h-24` (96px) with `overflow-y-auto`; `scrollHeight`
is 1116px and `<li>` count is **42** in both themes — every real task stays rendered
and reachable by scrolling *within* the cap. This is a LAYOUT bound, **not** a data
cull. Header shows `Linked tasks (42)` so the truncated-by-fold set is discoverable.

## Per-delta matrix

Each row: design authority + BEFORE + AFTER, light and dark. `rail-*` crops isolate
the right rail (Δ4 chips + Δ5 density); `composer-*` crops isolate the bottom pill
(Δ1); `replying-*` crops isolate the in-flight row (Δ2 + Δ3). Full-viewport frames
(`after-*` / `before-*`) show every delta in context.

| # | Delta | Design | BEFORE (light / dark) | AFTER (light / dark) |
|---|---|---|---|---|
| 1 | Composer → compact inline-send pill | `design-target.png` | `composer-before-demo-light.png` / `composer-before-demo-dark.png` (also `composer-before-live-*`) | `composer-after-demo-light.png` / `composer-after-demo-dark.png` (also `composer-after-live-*`) |
| 2 | Agent-replying → light inline indicator | `design-target.png` | `before-demo-light.png` / `before-demo-dark.png` (heavy card + terminal record) | `replying-after-demo-light.png` / `replying-after-demo-dark.png` |
| 3 | Abort-reply → inline by the indicator | `design-target.png` | `composer-before-demo-light.png` / `-dark.png` (old "Abort replies" in composer bar) | `replying-after-demo-light.png` / `replying-after-demo-dark.png` ("Abort reply") |
| 4 | Linked tasks → compact colored chips, active-first | `design-target.png` | `rail-before-live-light.png` / `rail-before-live-dark.png` (exhaustive list); `rail-before-demo-*` | `rail-after-demo-light.png` / `rail-after-demo-dark.png` (active-first); `rail-after-live-*` (42 real) |
| 5 | Density → Fresh tokens above the fold | `design-target.png` | `rail-before-live-light.png` / `rail-before-live-dark.png` (no This-thread visible); `before-live-*` frames | `rail-after-live-light.png` / `rail-after-live-dark.png` (Fresh tokens 7.6M visible); `after-live-*` frames |

## Full-viewport frames (all deltas in context)

- `after-live-light.png` / `after-live-dark.png` — real 42-task THR-061, R2. Fresh tokens above the fold.
- `before-live-light.png` / `before-live-dark.png` — real 42-task THR-061, origin/main. Fresh tokens below the fold.
- `after-demo-light.png` / `after-demo-dark.png` — THR-DEMO, R2. Composer pill + inline replying + Abort reply + active-first chips.
- `before-demo-light.png` / `before-demo-dark.png` — THR-DEMO, origin/main. Bulky composer + old abort location.
