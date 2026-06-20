# First-Pass Gap Analysis — Direction A "Pasture" vs Current `main`

> **STATUS: DRAFT — first pass, for `engineering_manager` to VALIDATE.**
> The authoritative gap call is **joint** (product_lead + engineering_manager).
> This is the product-side hypothesis of where Direction A diverges from what's
> shipped, sized roughly so we can prioritize the conversation — not an
> engineering estimate and not a commitment. Companion to
> `…-design-overhaul-PRD-draft.md`. Origin: THR-010 msg 44, TASK-411. 2026-06-16.

---

## 0. Method & the big caveat you must validate first

**⚠ CAVEAT #1 — I did NOT read the repos. There are none in my workspace.**
My `agent.yaml` has `repos: {}` and no `repos/` directory exists anywhere in the
runtime (`runtime/repos` absent; verified). I could not read `runtime`, `cli`,
`web`, or `docs/agent-guides/features-and-invariants.md` directly as the brief
intended.

**What I used as current-state ground truth instead:** your own
`engineering_manager-2026-06-16-design-handoff-package.zip` →
`product-update.md` (surfaces A–L) and `scope-and-diff.md`, which state they were
**verified against `main` source (HEAD `77150e0`)** with PR/commit citations. That
is a strong substitute, but it means:
- **Every "Current" cell below is cited to your docs, not to source I read.** Where
  I write a current behavior, treat it as *"per EM's 2026-06-16 docs."*
- **You hold the source.** Please correct any "Current" cell that's stale or wrong —
  that's the single most valuable thing you can do with this draft.

**Direction A side** is grounded in the live design bundle (screens + surfaces +
shell.js nav + 10 chat transcripts), read in full this session.

**Gap categories:**
- **MISSING** — Direction A wants it; nothing comparable ships today.
- **DIVERGENT** — both exist but behave/are-placed differently.
- **NEEDS-REWORK** — exists but Direction A reshapes it substantially.

**Effort (rough T-shirt, product guess — YOU re-size):** S / M / L / XL.
**Risk:** Low / Med / High (build risk + risk-to-the-honesty-principle / data-layer).

---

## 1. IA / navigation — the structural gaps (do these first; everything hangs off them)

| # | Desired (Direction A) | Current (per EM docs) | Category | Effort | Risk | Notes |
|---|---|---|---|---|---|---|
| IA-1 | Left **sidebar**, two groups (primary + "Operate") + footer; desktop window chrome | 9 flat **top tabs** | NEEDS-REWORK | L | Med | Touches every page's shell/layout. Native-shell chrome is new. |
| IA-2 | Default landing = **Home/Dashboard** | Default landing = **Threads** | DIVERGENT | S | Low | One-line route change + confirm intent. |
| IA-3 | **Spend** as a dedicated page | Dashboard panel only; no page | MISSING | L | Med | New surface; depends on Q1 (dollar model). |
| IA-4 | **Dreams** as a dedicated surface (feed + KB-candidate queue) | None; dream-threads indistinguishable | MISSING | L | Med | New surface + dream→KB candidate gate. |
| IA-5 | **Schedule** as a dedicated surface | CLI-only (`work-hours`) | MISSING | L | Med | New surface; **depends on web API mirror that is on an UNMERGED branch** (current §I). |
| IA-6 | **Assistant = omnipresent dock** (⌘K) | Dedicated `/assistant` xterm page | NEEDS-REWORK | L | Med | Replaces/relocates terminal; global ⌘K affordance is new. Q3. |
| IA-7 | **Settings = dedicated page** w/ sub-nav | Modal **dialog** from gear | NEEDS-REWORK | M | Low | Dialog→page; add `/settings` route; keep the editable Org fields. |
| IA-8 | **Jobs tab retired**; jobs contextual; no Jobs list | Dedicated **Jobs** tab | DIVERGENT | S–M | Med | Removing a surface; Q6. Don't delete the list without ruling. |
| IA-9 | **KB → "Knowledge"** label + folder rail | Tab "KB"; flat | DIVERGENT + NEEDS-REWORK | M | Low | Rename + folder navigation. |
| IA-10 | Nav semantic **grouping** (primary vs Operate) | Flat | DIVERGENT | S | Low | Cosmetic-ish once IA-1 lands. |

**Rollup:** the IA is the dominant lift. Three brand-new surfaces (Spend, Dreams,
Schedule), two major reworks (Assistant dock, Settings page), one removal (Jobs
tab), plus the shell/sidebar rebuild. **Sequence recommendation:** land the
shell/sidebar (IA-1) + routing (IA-2) first, then the three greenfield surfaces in
parallel, since they have the fewest dependencies on existing code.

---

## 2. Per-surface behavioral gaps

### Home / Dashboard
| Desired | Current | Category | Effort | Risk |
|---|---|---|---|---|
| Calm narrative greeting + "Today" heartbeat + counters | Two-column board, status strip, heartbeat exists | NEEDS-REWORK | M | Low |
| "This week's burn" glance that **links to Spend** (no duplication) | "Top token threads" panel lives *on* the dashboard | NEEDS-REWORK | S | Med — depends on Spend existing (IA-3) |
| Auto-resolution shown as positive metric ("6 escalations cleared by supersede") | Auto-resolution invisible (current §H) | MISSING | S | Low |
| Tightened escalation triage (kind→verb), demoted long tail | "Right column is a text wall" (current §A) | NEEDS-REWORK | M | Med — **Q2 ownership** |

### Threads
| Desired | Current | Category | Effort | Risk |
|---|---|---|---|---|
| List leads with last-speaker + avatar stack + status pills | Cards show subject/chip/turn counter | NEEDS-REWORK | M | Low |
| **System/tool-run events visually distinct** from prose (embedded run cards, task-ref cards) | Flat transcript; system events inline (current §B) | NEEDS-REWORK | M | Med — needs the daemon to expose structured event/tool-run data |
| Turn budget visible before cap | Invisible until near cap (current §B) | NEEDS-REWORK | S | Low |
| @mention routing | Broadcast, no @mention routing | MISSING / DIVERGENT | M | **High (P1)** — must not imply routing that doesn't exist |

### Tasks
| Desired | Current | Category | Effort | Risk |
|---|---|---|---|---|
| Bidirectional supersede links (`↳ supersedes` / `→ TASK-407`) | "No link from superseded task to continuation" (current §H) | MISSING | M | Med — needs the link stored/queryable |
| Connected vertical **chain timeline**, blocked node names blocker | Recall tree exists but "tree legibility poor" (current §C) | NEEDS-REWORK | M | Low |
| Brief = raw monospace markdown + "Show full" | Brief + collapsible exists | DIVERGENT (mostly aligned) | S | Low |
| List grouping `Status / Agent / Thread` | Status + team filters | NEEDS-REWORK | S | Low |

### Agents
| Desired | Current | Category | Effort | Risk |
|---|---|---|---|---|
| **Editable** detail (executor switch, repos, prompt, autonomy toggle) in roomy two-pane | **Read-only** drawer (repos + prompt) (current §L) | NEEDS-REWORK | L | **High** — write paths for executor/repos/prompt; autonomy toggle is a **safety-sensitive** new control |
| Accountability metrics on the agent (tasks done, accept rate) | Recent tasks list in drawer | MISSING | M | Med — must be real stored counts (P1) |
| Inline executor switch | `set-executor` is CLI-only (current §J) | MISSING (web) | M | Med |

### Knowledge (KB)
| Desired | Current | Category | Effort | Risk |
|---|---|---|---|---|
| Folder rail navigation | Flat KB page | NEEDS-REWORK | M | Low |
| **Dream-candidate review gate** (Accept/Edit/Dismiss, in-context banner) | No candidate affordance (current §F/§J) | MISSING | M | Med — depends on Dreams + candidate data model |
| Usage signal ("used by N agents · v3") on entries | `kb stats` is CLI-only (current §J) | MISSING (web) | S | Low — data exists, surface it |
| Citation badges / "load-bearing" | (correctly absent) | **N/A — explicitly cut v1** | — | — (needs `kb_consulted` events first) |

### Artifacts
| Desired | Current | Category | Effort | Risk |
|---|---|---|---|---|
| 3-col card grid + type filter + provenance metadata | Flat table (current §D) | NEEDS-REWORK | M | Low |
| PR detail: checks (incl. maker-checker + founder-gate as CI checks), files, diff | (no rich PR detail described) | MISSING | L | Med — needs CI/review/job status exposed per artifact |
| Folder/nested-key browsing | Backend supports nested keys; **web renders flat** (current §D) | **DIVERGENT — Direction A ALSO flat** | — | — | Direction A does **not** solve folders → **Q4** |

### Spend (greenfield)
| Desired | Current | Category | Effort | Risk |
|---|---|---|---|---|
| Full page: window toggle, fresh-vs-cache chart, by team/agent/thread/model, top-threads, export | Dashboard panel + `happyranch tokens` CLI; **no page** (current §E) | MISSING | L | **High** — **Q1 (dollar model)** must be ruled; **must obey churn invariant** (cache separate, never folded) per KB `token-usage-surface-ownership-doctrine` |
| Honest non-blank model labels | Cryptic labels today (current §E) | NEEDS-REWORK | M | Med — O1–O4 of the doctrine |

### Dreams (greenfield)
| Desired | Current | Category | Effort | Risk |
|---|---|---|---|---|
| Reflection feed + dream detail (quote/stats/doc/candidates) | None (current §F) | MISSING | L | Med — needs daemon to expose dream runs + reflections + candidates as queryable objects |
| KB-candidate queue with confidence + accept flow | None | MISSING | M | Med — shared with Knowledge gate |
| Dream-originated threads marked as such | Indistinguishable (current §F) | MISSING | S | Low |

### Schedule (greenfield)
| Desired | Current | Category | Effort | Risk |
|---|---|---|---|---|
| Overview + per-agent work-hours + named recurring wakes + "While you were away" + behavior toggles | **CLI-only**, web mirror on **unmerged branch** (current §I) | MISSING | L | **High** — gated on backend/web API that isn't on `main` yet; biggest unknown |

### Assistant (dock)
| Desired | Current | Category | Effort | Risk |
|---|---|---|---|---|
| Omnipresent dock, ⌘K, inline `ran:` command transparency, one-click action chips | `/assistant` page + xterm terminal (current §G) | NEEDS-REWORK | L | **High** — action chips that *execute* runtime ops must route through founder gates (P1/safety); global dock state |
| Assistant config (Init/Repair/status) in Settings | In Settings dialog already (current §K/§G) | DIVERGENT (placement) | S | Low |

### Settings (page)
| Desired | Current | Category | Effort | Risk |
|---|---|---|---|---|
| Dedicated page + sub-nav + `/settings` route | Dialog only; no route (current §K) | NEEDS-REWORK | M | Low |
| Agent-name **chips w/ autocomplete** | Comma-separated text fields (current §K) | NEEDS-REWORK | S | Low |
| Per-field live-vs-restart labeling | Partial ("Restart required" badges exist) | NEEDS-REWORK | S | Low |
| Editable Org (dreaming/threads) | **Already shipped** (#102) | **Mostly DONE** | — | Low — reuse |

### Audit
| Desired | Current | Category | Effort | Risk |
|---|---|---|---|---|
| Day-grouped timeline, event-type legend/filter, query language, export, per-event cost | Audit + Traces exist (current §E/§J) | NEEDS-REWORK | M | Low |
| **Owns the escalation Open/Resolved loop** (chat decision) | Not in prototype screen | UNRESOLVED | — | — | **Q2** — don't build until ownership ruled |

### Jobs
| Desired | Current | Category | Effort | Risk |
|---|---|---|---|---|
| Job **detail**: verbatim command/diff + "If approved" cascade + uniform 2-step confirm | Jobs surface exists | NEEDS-REWORK | M | Med — "If approved" cascade must be real downstream state (P1) |
| **No Jobs list** (retire tab) | Jobs tab exists | DIVERGENT | S–M | Med — **Q6** |
| No danger tiers | (verify none today) | N/A — cut | — | — |

---

## 3. Effort/risk rollup (rough, product view)

**Biggest lifts (XL/L):** the shell/sidebar rework (IA-1) + three greenfield
surfaces (Spend, Dreams, Schedule) + Assistant dock + editable Agents. Of these,
**Schedule is the highest-uncertainty** (depends on a backend/web mirror not yet on
`main`), and **Spend + Assistant carry the most honesty/safety risk** (cost-model
correctness; action chips that execute privileged ops).

**Quick wins (S, low risk):** default-landing route (IA-2), auto-resolution positive
metric on Home, turn-budget visibility, KB usage surfacing, Settings autocomplete,
nav grouping. Several of these are "surface data the daemon already stores."

**Already largely done:** editable Org settings (#102) — reuse, don't rebuild.

**Risk concentration — the honesty principle (P1):** the highest-risk items aren't
the hardest to build, they're the ones that tempt the UI to assert what the daemon
can't prove — @mention routing, "If approved" cascades, accountability metrics,
agent autonomy semantics, and any cost/dollar figure. Every one of these needs a
"does the store actually back this?" check with you before it ships.

---

## 4. Top risks (draft)

1. **No repo access on my side** → current-state cells may be stale; **you must
   validate.** (Caveat #1.)
2. **Schedule depends on unmerged backend** → can't build the surface honestly until
   the web API lands; may need to sequence behind it.
3. **Spend dollar model unresolved (Q1)** → blocks a consistent cost story across
   Home/Spend/Threads/Audit; building before the ruling risks rework.
4. **Assistant action chips + agent autonomy toggle** → safety-sensitive; must route
   through founder gates and never bypass approval on merges/protocol edits.
5. **Two prototype renderings disagree** (HTML vs JSX) on Spend dollars, agent
   executors, dashboard triage → don't treat either as canonical without a ruling.
6. **Chat-vs-prototype tensions (Q2 escalation ownership, Q6 Jobs list)** → building
   the wrong owner means tearing it out later.

---

## 5. Gap-closure questions for `engineering_manager` (so we can converge)

**On the ground truth:**
1. Are my "Current" cells accurate against `main`? Which are stale/wrong? (You have
   the repos; I don't.)
2. Is there a current-state behavior or backend capability the design ignores that we
   should preserve (a load-bearing invariant from `features-and-invariants.md`)?

**On the structural sizing:**
3. Do you agree the shell/sidebar (IA-1) + routing (IA-2) should land first, with the
   three greenfield surfaces in parallel? Where would *you* sequence differently?
4. **Schedule:** what's the real status of the web/API mirror for `work-hours`
   (current §I says unmerged)? Is it close, or is Schedule effectively blocked?
5. For each greenfield surface (Spend, Dreams, Schedule) — does the daemon **already
   store and expose** the data the design needs (dream reflections + candidates;
   per-agent work-hours + wakes; reconciled token/cost rollups), or is there a
   backend gap behind the UI gap?

**On the honesty-principle hotspots (P1):**
6. **@mentions** in Threads — does the daemon actually route on @mention, or is it
   pure broadcast? (Determines whether the affordance is honest.)
7. **Jobs "If approved" cascade** and **agent accountability metrics** (tasks done /
   accept rate) — are these real stored/queryable facts, or would the UI be inventing
   them?
8. **Agent autonomy toggle** — what does "low-risk action" mean concretely, and can
   you guarantee it never bypasses the founder gate on merges/protocol edits?

**On the open product decisions (need a ruling, several Founder-level):**
9. **Q1 — Spend dollar model:** are executors flat-rate local (→ `$0.00`, tokens are
   the budget) or metered (→ real dollars)? This is partly a fact you can confirm.
10. **Q2 — escalation queue ownership:** Home (tightened list, as the prototype
    shows) or Audit (Open/Resolved loop, as chat decided)?
11. **Q4 — Artifacts folders:** ship flat for v1 (design's answer) or build folder
    browsing now?
12. **Q6 — Jobs list:** accept "no Jobs index, contextual only" (retire the tab), or
    keep a lightweight "jobs awaiting you" list?
13. **Q7 — statefulness:** is v1 click-through fidelity, or do approve/save actions
    become real in the first cut?

---

*End of first-pass gap analysis. Authoritative gap call is joint — over to you, EM.*
