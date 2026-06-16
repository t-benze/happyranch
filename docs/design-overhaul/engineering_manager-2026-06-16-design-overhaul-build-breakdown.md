# BUILD BREAKDOWN — HappyRanch Design Overhaul (Direction A "Pasture")

> **STATUS: Engineering build plan for the v1 overhaul.** Decomposes the
> **authoritative** `product_lead-2026-06-16-design-overhaul-PRD-build-spec.md`
> (TASK-415, LOCKED) into phased, worker-sized items. This is the **visible
> plan** the team and founder track against.
>
> **Source of truth:** the build-spec PRD (rulings per founder msgs 62 + 66,
> THR-010, 2026-06-16) + the validated gap analysis
> (`engineering_manager-2026-06-16-design-overhaul-gap-analysis-validated.md`,
> TASK-413, `origin/main @ 77150e0`). Sequencing follows the build-spec **§7
> honesty-tier ordering** — dependency order, **NOT a dated commitment**.
> Author: `engineering_manager`, TASK-416, 2026-06-16.
>
> **Branch discipline:** all overhaul work lands on the shared `design-overhaul`
> integration branch (cut from `origin/main @ 77150e0`, TASK-412). Every PR
> targets `design-overhaul` — **never `main`**. Each sub-task: worktree off
> `design-overhaul`; TDD for logic; `gitnexus_impact` before editing any
> symbol (report **collateral** HIGH/CRITICAL); `qa_engineer` PASS + CI green
> before merge into `design-overhaul`; doc-parity
> (`docs/agent-guides/features-and-invariants.md`) in the same PR when behavior
> changes. **Nothing under `protocol/`. No auth / permission-model / Codex-sandbox /
> allow-rule changes — if a sub-task appears to need one, STOP and escalate.**

---

## 0. How to read this breakdown

- **Class** mirrors the build-spec legend: **RENDER-ONLY** (pure frontend; data
  already stored + exposed), **DERIVE** (new read/aggregation route, **no schema
  change**, within EM delegation authority), **MIGRATION** (the single approved
  schema change, A4).
- **Effort** is a rough T-shirt size (S / M / L) for one delegation, not a
  timeline.
- **Guardrail** names the load-bearing invariant or P1-honesty constraint the
  reviewer rejects on first.
- **Dependency** is the build-order predecessor. The dominant dependency is
  **IA-1 (the shell)** — every surface re-parents into it, so it is built
  **first** (Phase 1).

**v1 tally (from the validated gap analysis):** ~28 RENDER-ONLY line-items +
6 DERIVE routes + **the single A4 migration** = ~34 items + A4. Everything
needing a new persisted store, new daemon behavior, or a permission-model change
is **deferred to the founder-gated post-v1 set** (build-spec §6) and is **out of
this build**.

---

## 1. Phase map (per build-spec §7)

| Phase | Theme | Contents | Gate |
|---|---|---|---|
| **P1 — Foundation** | The shell everything hangs off | IA-1 sidebar shell + window chrome · IA-2 Home default route · IA-10 nav grouping · **A4 migration** | **THIS ROOT (TASK-416).** A4 + IA shell ship as two discrete PRs. |
| **P2 — Render-only surfaces (parallel once shell lands)** | "Homes for the homeless" + reshapes | Spend · Dreams (read) · Schedule (read) · Agents edit · Tasks reshape · Threads reshape · Settings page · Knowledge folder rail · Artifacts flat grid · Audit timeline/filter · Jobs retire+detail · Home heartbeat | **Follow-on roots.** Each surface is its own delegation; parallelizable after P1 merges. The 6 DERIVE routes land alongside. |
| **P3 — Assistant hybrid dock** | Conversational operator dock | IA-6 / §4.10 ⌘K omnipresent Assistant dock + gated action chips | **Follow-on root**, carries the **TASK-414 guardrails** (see §5). |

> **This root (TASK-416) executes Phase 1 ONLY.** P2 + P3 are deferred to
> follow-on roots and are listed here so the plan is visible end-to-end.

---

## 2. PHASE 1 — Foundation (THIS ROOT)

### 2a. A4 migration — `composed_from_dream_id` on `threads`  *(DISCRETE PR)*

| Field | Value |
|---|---|
| **Class** | **MIGRATION** (the single approved v1 schema change; build-spec §4.8 / §6 promotion / ruling A4) |
| **Effort** | S |
| **Dependency** | None. Independent of the shell; ships first/in parallel. |
| **What ships** | Additive **nullable** column `composed_from_dream_id TEXT` on the `threads` table + the dream-originated-thread **marker logic** (set the column when a thread is composed by a dream/nightly-reflection) + read-through on the thread model/select so the marker is visible to Threads list/detail, Home, and Audit consumers. |
| **Guardrail (LOAD-BEARING)** | **Additive nullable ONLY. NO `ALTER … DROP`, NO column rename, NO overloaded-column reuse, NO backfill.** Forward/back-compatible with v0 (DB-backed enrollments) and v1 (flat single-org) runtimes. **Follow the existing idempotent additive-column pattern in `runtime/infrastructure/database.py`** — the near-identical precedent is `composed_from_task_id` (ALTER swallowed by `except sqlite3.OperationalError`, plus a partial index `WHERE composed_from_dream_id IS NOT NULL`). Mirror the `ThreadRecord` model + `insert_thread` + `_row_to_thread` (`"... in keys"` guard) wiring used for `composed_from_task_id`. |
| **TDD** | **Required** for the marker logic (a thread composed by a dream gets the column set; a coordination thread leaves it NULL; existing rows read back NULL without error). |
| **Review** | Its **own focused PR** into `design-overhaul` so the schema change gets isolated review. dev → code_reviewer (codex) → qa_engineer. qa_engineer's integration scope **does** apply if the diff touches callback routes / DB lifespan. |
| **Escalation tripwire** | If the marker requires changing the permission model, a `protocol/` doc, or any non-additive schema op — STOP and escalate. |

### 2b. IA shell foundation (IA-1 + IA-2 + IA-10)  *(SEPARATE PR)*

| # | Item | Class | Effort | Dependency | Guardrail |
|---|---|---|---|---|---|
| IA-1 | **Grouped left sidebar + desktop window chrome**, retiring the ~9-tab `TopBar`. Primary group `Home · Threads · Tasks · Agents · Knowledge · Artifacts`; **Operate** group `Spend · Dreams · Schedule · Audit`; footer `Settings` (+ founder identity, **theme toggle migrated from TopBar**, org switcher). | RENDER-ONLY (L) | L | None (build first) | **P4** (retire top-tab bar → left rail). **P5** theme toggle must persist across nav after moving out of `TopBar`. Touches **every** page layout (re-parent existing pages into the new `AppShell`). **No new stores.** |
| IA-2 | **Default landing = Home** (was Threads). Repoint `RootRedirect` and the per-org index redirect (`NavigateToThreads`) to the Home/Dashboard route. | RENDER-ONLY (S) | S | None | **AC1** (build-spec §4.1): default route resolves to Home. **Behavior change → doc-parity** (`features-and-invariants.md`) in the same PR. |
| IA-10 | **Nav grouping** (primary / Operate) — cosmetic once IA-1 lands. | RENDER-ONLY (S) | S | IA-1 | Grouping only; no logic. |

**Scope decisions for Phase 1b (stated to prevent rework):**
- **Home == the existing `DashboardPage`** (`features/dashboard`). Phase 1b only
  makes it the **default landing** and re-parents it into the new shell; the
  Home **content** rework (heartbeat, org pulse, rollups — §4.1) is **P2**.
- **Not-yet-built Operate surfaces (Spend / Dreams / Schedule)** render the
  grouped nav entry routing to a **lightweight "coming in the design overhaul"
  placeholder page** so the IA is demonstrable and testable as the real shell.
  Each P2 surface swaps its placeholder for the real page + nav wiring. This
  keeps Phase 1b **render-only with no dead links**.
- **Jobs** (`IA-8` retirement) is **NOT retired in Phase 1b** — it depends on the
  Home "awaiting-you" rollup + Audit history, which are **P2**. Phase 1b leaves
  the `/jobs` route reachable (not featured in the new primary/Operate rail);
  formal retirement lands in P2 with its replacements, to avoid orphaning jobs.
- **Frontend only, render-only, no new stores.** No backend/route changes.

---

## 3. PHASE 2 — Render-only surfaces (FOLLOW-ON ROOTS, parallel once P1 merges)

Each row is a candidate standalone delegation. All depend on **IA-1**. Class is
RENDER-ONLY unless noted; the 6 DERIVE routes (§4) land alongside the surface
that consumes them.

| # | Surface (spec §) | Class | Effort | Key guardrail (reviewer rejects-on-first) |
|---|---|---|---|---|
| S-1 | **Spend** — token page (§4.7, IA-3) | RENDER-ONLY | L | **P7 churn invariant [BINDING]:** `total = input + output + reasoning`; **cache reads in a SEPARATE column, never folded into churn or used as a ranking key** (KB `token-usage-surface-ownership-doctrine`). Dollars = `$0.00 / not metered` (Q1). Model labels non-blank, never a fabricated correction (O1–O4). |
| S-2 | **Dreams** — reflection feed + detail, read (§4.8, IA-4) | RENDER-ONLY | L | Reflection text = the agent's **actual stored** reflection (P1). **Consumes the A4 marker.** "Quiet dream" is a first-class calm state (P2). |
| S-3 | **Schedule** — read surface (§4.9, IA-5) | RENDER-ONLY | L | **No affordance implying you can create a new named recurring wake** (D6 deferred). Reflects real scheduler state, not a mock (P1). |
| S-4 | **Agents** — editable detail wire-up (§4.4) | RENDER-ONLY | M | **NO autonomy toggle** (A1 deferred). Real saves via existing write routes (Q7). Accountability metrics are **real derived counts** (see D-2), never estimates. |
| S-5 | **Threads** — list + detail reshape (§4.2) | RENDER-ONLY | M | **@mention BROADCAST-ONLY** — no UI implying routing (A2/P1, dominant lens here). System/dispatch events visually distinct; **no synthesized in-transcript "ran:" cards** (D7 deferred). **Consumes the A4 marker.** Turn budget X/500 visible. |
| S-6 | **Tasks** — list + detail reshape (§4.3) | RENDER-ONLY | M | **List, not kanban.** Brief = raw monospace markdown, **no slot parser** (P1). Bidirectional lineage from real `revisit_of_task_id` / `walk_revisit_chain()`. Blocked node names its real `blocked_on`. |
| S-7 | **Settings** — page (not dialog) (§4.11, IA-7) | RENDER-ONLY | M | Real bookmarkable `/settings` route. **Per-field live-vs-restart labels match real daemon behavior** (P1). Reuse shipped editable Org fields (#102). Agent-name chips autocomplete from real roster. |
| S-8 | **Knowledge** — rename + folder rail (§4.5, IA-9) | RENDER-ONLY | M | **"viewed N× (CLI)" ONLY** — drop "used by N agents" / "v3" (K1/P1, KB `kb-view-tracking-caller-signal`). **No citation/load-bearing badges** (D5 deferred). |
| S-9 | **Artifacts** — flat recency grid (§4.6) | RENDER-ONLY | M | **Flat grid, no folder tree** in v1 (Q4). **No PR "checks" panel** with un-stored states (A5/P1). Provenance limited to stored fields. |
| S-10 | **Audit** — timeline + legend-as-filter (§4.12) | RENDER-ONLY | M | **Owns** resolved-escalation + completed/past-jobs history (Q2/Q6); reads the **same store** as Home's active triage — no double-ownership. Tokens, not dollars. Query DSL deferred (D9). |
| S-11 | **Jobs** — retire tab + job detail (§4.13, IA-8) | RENDER-ONLY removal | M | **No standalone Jobs index** (Q6). **No danger-tier ranking; uniform two-step confirm.** Gated on the Home rollup + Audit history existing first. |
| S-12 | **Home** — heartbeat / counters / org pulse / rollups (§4.1) | RENDER-ONLY (+ D-1) | L | **P1:** every number traces to a stored fact — no forecast/interpreted values. Home token-burn == Spend same-window figure (P3, single source of truth). Owns **active** escalation triage + jobs **awaiting-you** rollup only (Q2/Q6). |

---

## 4. PHASE 2 DERIVE routes (6) — new read/aggregation, NO schema, EM authority

These land alongside the surface that consumes them. Each is a new read/query
route; **none touch the schema** and all sit within EM delegation authority.

| # | DERIVE route | Backed by (existing) | Consumed by | P1 guardrail |
|---|---|---|---|---|
| D-1 | **Auto-resolution metric** ("N escalations cleared by supersede") | count `audit_log action='escalation_superseded'` rows | Home (§4.1) | Real counts only. |
| D-2 | **Agent accountability metrics** ("42 done · 88% accept") | tasks filtered by agent + `audit_log review_verdict` (APPROVE/PASS ÷ verdict rows) | Agents (§4.4) | Real counts, never estimates. |
| D-3 | **KB/Dreams candidate Accept/Edit/Dismiss** mutation route | `dream_kb_candidates` `status` / `promoted_kb_slug` cols (exist) | Knowledge (§4.5) + Dreams (§4.8), shared route | Real mutation (Q7). |
| D-4 | **Jobs "if-approved" cascade** (forward projection) | `blocked_on_job_ids` + `list_tasks_blocked_on_jobs()` | Jobs detail (§4.13) | Must reflect **real** downstream tasks — no invented effects. |
| D-5 | **Audit export** read route | existing audit_log | Audit (§4.12) | Export of stored events only. |
| D-6 | **Upcoming/past wakes listing** | existing wake-execution rows | Schedule (§4.9) | Lists wake **executions**, not editable wake **definitions** (D6). |

---

## 5. PHASE 3 — Assistant hybrid dock (FOLLOW-ON ROOT)

| # | Item | Class | Guardrail (carries TASK-414 constraints — DO NOT relax) |
|---|---|---|---|
| IA-6 / §4.10 | **Assistant = omnipresent ⌘K dock** + inline real "ran:" transparency cards + **gated** one-click action chips; the existing `/assistant` xterm becomes the "Open full session" escape hatch. | RENDER-ONLY + gated chips | **Structured frames inside the EXISTING assistant WS. Bearer-auth + PTY contract FROZEN. NO new endpoint.** Chips are **founder-clicks that route through the EXISTING approval/job gate — a chip NEVER bypasses merge or protocol-edit approval and NEVER lets the assistant self-approve.** Build approach is **TASK-414's call** (hybrid: structured React chat + ⌘K primary, xterm retained). |

> Phase 3 is **not started in this root.** When it is dispatched, the brief
> must embed the TASK-414 guardrails verbatim and any new-endpoint / self-approve
> temptation is an **escalation**, not an implementation.

---

## 6. The "no" list (out of this build — re-proposing needs a Founder reversal)

Carried verbatim from build-spec §5 so reviewers can reject on sight: Jobs
danger-tiers / progress bars / dry-run; job-detail **stored diff** preview; agent
failure-pattern psychoanalysis / invented clusters; KB citation badges & "used by
N agents · v3"; Tasks kanban; Tasks 8-slot brief parser; **real-dollar figures on
any v1 surface**; **@mention routing** affordance; agent **autonomy toggle**;
in-thread agent-own-execution "ran:" cards; Direction B "Mission Control";
reintroducing Talks. All **§6 founder-gated deferred items (D1–D9)** are out of
this build.

---

## 7. Invariant guardrails referenced (quick index)

- **A4 / migration discipline** — additive nullable only; no alter/drop; no
  overloaded-column reuse; v0/v1 compat; mirror `composed_from_task_id` in
  `database.py`. (Phase 1a.)
- **P7 token churn** — `total = input + output + reasoning`; cache reads in a
  separate column, never folded in or ranked on. (KB
  `token-usage-surface-ownership-doctrine`.) (Spend, Home, Audit, Threads.)
- **P1 honesty (dominant lens)** — every datum traces to a stored fact; no
  synthesized interpretation. Reviewers reject on P1 first, visuals second.
- **Permission model untouched** — Claude `--allowedTools`, Codex sandbox,
  opencode `permission.bash`, baseline allow-rule, auth/bearer flow: **no
  changes.** Any apparent need → escalate.
- **`protocol/` is the founder's surface** — no edits anywhere in this build.

---

*End of build breakdown. Inputs: `product_lead-2026-06-16-design-overhaul-PRD-build-spec.md`
(TASK-415, LOCKED) + `engineering_manager-2026-06-16-design-overhaul-gap-analysis-validated.md`
(TASK-413). Branch: `design-overhaul`. — engineering_manager, TASK-416.*
