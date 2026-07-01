# VALIDATED Gap Analysis — Direction A "Pasture" vs Current `main`

> **STATUS: AUTHORITATIVE (engineering-validated).** Supersedes the "Current"
> column of `product_lead-2026-06-16-design-overhaul-gap-analysis-draft.md`.
> Validated by `engineering_manager` against source at **origin/main @ `77150e0`**
> (a fresh worktree, NOT the stale local tree at `4257227`). Companion to
> product_lead's PRD draft. Origin: THR-010, TASK-413. 2026-06-16.

---

## 0. Method — how this was validated

product_lead had **no repo access** (`repos: {}`), so every "Current" cell in
their draft was inferred from my 2026-06-16 design-handoff docs, not from source.
This pass reads the **actual code on `origin/main @ 77150e0`** and confirms,
corrects, or refines each cell. Validation covered `runtime/` (Python daemon +
orchestrator + infrastructure store) and `web/src/` (React surfaces). Citations
are `path:line` against `77150e0`.

**GitNexus status (per brief step 3):** GitNexus MCP is **up but its happyranch
index is STALE** — the freshest index is commit `c13c23d` (2026-06-13), which
predates `77150e0`. Blast-radius numbers below are therefore *approximate*
(indexed against a slightly older tree); the symbols queried all still exist on
`77150e0`. Where I rely on GitNexus I say so; the per-symbol structural tracing
itself was re-confirmed manually against `77150e0`. **GitNexus indexes Python
only** (LRN-053) — it is **N/A for the frontend/TSX work**, which is the bulk of
this overhaul, so most rows below are "GitNexus: N/A (frontend)".

### Classification legend (the load-bearing lens — brief step 4)

Every design element that renders data is classified by what *backs* it today:

- **RENDER-ONLY** — daemon already **stores AND exposes** the data via a route;
  the gap is pure frontend. Honest to ship.
- **DERIVE** — data exists in existing tables but there's **no endpoint** yet; a
  new **read/aggregation route or query** is needed. **No schema change** → within
  my delegation authority. Honest once computed from the real rows.
- **NEW-STORE 🚩** — requires a **new column / table / persisted field** (or a new
  event stream). Per the engineering constraints this implies a **SQLite
  migration / new persisted field** and is a **FOUNDER ESCALATION**. I author **no**
  migration here; I flag it.
- **NEW-LOGIC 🚩** — requires new **behavioral** logic (e.g. message routing) that
  changes daemon semantics, not just a store. Honesty-sensitive; flagged.

---

## 1. The corrections that matter most (read these first)

product_lead's draft was structurally excellent; four "Current" cells were
materially wrong or stale, and they change sequencing:

1. **Schedule is NOT blocked on an unmerged branch.** (Draft IA-5, §Schedule, Top
   Risk #2 all assumed the work-hours web/API mirror is unmerged.) **WRONG on
   `77150e0`:** `runtime/daemon/routes/work_hours.py` ships a full HTTP API
   (`GET …/work-hours/status`, `…/work-hours`, `…/work-hours/{id}`,
   `POST …/work-hours/{id}/spawn`) and `web/src/lib/api/work-hours.ts` already
   exists. The read surface is **RENDER-ONLY** (no `web/src/features/schedule/`
   folder yet, but the data + API client are on `main`). Schedule drops from
   "highest-uncertainty / maybe-blocked" to a buildable read surface. *Caveat:*
   the store records **wake-execution rows** (`work_hours` table), not editable
   **named-recurring-wake definitions** — see §Schedule for the partial.

2. **Dreams backend is fully on `main`.** (Draft called Dreams greenfield "needs
   daemon to expose dream runs + reflections + candidates.") `routes/dreams.py` +
   `dreams` and `dream_kb_candidates` tables + `web/src/lib/api/dreams.ts` all
   ship. Dream feed/detail are **RENDER-ONLY** on the read side. Two real gaps
   remain (see §Dreams): the **Accept/Edit/Dismiss** candidate flow has **no
   mutation route** (DERIVE — columns exist, no `PATCH`), and **dream-originated
   threads are NOT marked** (NEW-STORE 🚩 — no `composed_from_dream_id` on threads).

3. **Supersede links and structured thread events already exist.** (Draft marked
   both "needs the link stored/queryable" / "needs daemon to expose structured
   event data".) **Both already stored & exposed:** `revisit_of_task_id` +
   `get_direct_revisits()` + `walk_revisit_chain()` (bidirectional, queryable);
   thread `ThreadMessage.kind` (MESSAGE/DECLINE/SYSTEM) + `system_payload.kind_tag`.
   Both are **RENDER-ONLY**, not backend gaps.

4. **Agent edit write-paths already exist.** (Draft: editable Agents = "write paths
   for executor/repos/prompt".) The routes already ship (`POST /agents/manage`
   update; `PUT /agents/{name}/executor`; `POST /agents/{name}/repos`); the web
   drawer is just read-only. Wiring those into the UI is **RENDER-ONLY/wire-up**.
   **The exception is the autonomy toggle** — no such field exists anywhere, and it
   is **NEW-STORE 🚩 + permission-model** (double-flagged; see §Agents).

The net effect: **the greenfield surfaces are far less greenfield than the draft
assumed.** The genuine hard blockers collapse to a small set of NEW-STORE items,
dominated by the **dollar/cost meter (Q1)**.

---

## 2. The two decision questions, answered from code (brief step 5)

### Q1 — Spend: tokens vs real dollars — **DEFINITIVE**

**The daemon stores token counts only. No real-currency cost is metered anywhere.**

- The token store `session_token_usage`
  (`runtime/infrastructure/database.py:548-567`) has columns: `input_tokens`,
  `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`,
  `reasoning_tokens`, `model`, `executor`, `usage_raw_json`, `scope_type`,
  `scope_id`, `thread_id`, `invocation_purpose`, `created_at`. **There is no
  cost/price/usd/rate column.**
- `task_results.estimated_cost REAL` exists in the schema
  (`database.py:458-475`) **but is never populated** — every `insert_task_result()`
  call site omits it (`routes/tasks.py` completion path, `orchestrator/run_step.py`).
  It is always SQL `NULL`.
- The dashboard's `compute_spend_today()`
  (`runtime/orchestrator/dashboard_summary.py:114-128`) sums
  `task_results.estimated_cost` → **always returns `0.0`**. Its own docstring
  admits "the spec's pseudocode references a `token_usage.cost_usd` column that
  does not exist."
- Executors are local CLIs. The Claude CLI *emits* a `costUSD` field in its
  stream-json, but the executor parser (`orchestrator/executors.py`) extracts
  **token counts only** and **discards `costUSD`** — it is never persisted.
- `happyranch tokens` (`cli/commands/tasks.py:390-539`) prints token columns
  (Input / Output / CacheR / Total) — **no dollars**.

**What the daemon can HONESTLY render today:** token counts — by team / agent /
thread / model / scope, with **cache separated from fresh** (the churn invariant
is intact: `TokenUsage.total` excludes cache, `database.py` aggregation sums cache
in its own columns). For dollars it can only honestly show `$0.00` / "not metered".

**What a real-dollar figure REQUIRES (= FOUNDER ESCALATION 🚩):** a **cost meter** —
(1) a per-model **price table** (new table/config: input/output/cache rates),
(2) cost computed at token-capture time, (3) a **persisted `cost_usd`** field (new
column on `session_token_usage`, or finally populating `task_results.estimated_cost`).
That is a **new persisted field / SQLite migration** → I will not author it; the
founder must rule. GitNexus: the read side is low-blast (`compute_spend_today`
upstream = LOW, only feeds `get_dashboard_summary`), so the risk is the new store,
not edit fan-out.

**Engineering recommendation for the ruling:** ship Spend v1 with **tokens as the
budget unit** (honest, zero new schema) and render dollars as `$0.00 / not
metered` *or* explicitly approve building the cost meter as a separate,
founder-gated workstream. Do **not** let any prototype's hardcoded dollar figure
ship as if real (P1).

### Q2 — Escalation queue: Home vs Audit — **DEFINITIVE: UX choice, not a data constraint**

Both placements are served by the **same stored data**: tasks with
`status='blocked' AND block_kind='escalated'` (`runtime/models.py`).

- Home/Dashboard reads it via `compute_escalations_open()`
  (`dashboard_summary.py:345-372`) — joins `tasks` to the `escalation` audit row.
- Audit reads the same escalations via `query_audit_logs(action='escalation' /
  'escalation_resolved')` (`routes/audit.py:17-39`), folded Open/Resolved in
  `web/src/features/audit/EscalationsTab.tsx`.

Both ultimately read the **same `tasks` escalation state + `audit_log` escalation
events**. **Placing the queue on Home or Audit (or both) is purely a UX/routing
decision — there is no data constraint either way.** GitNexus:
`compute_escalations_open` upstream = **LOW** blast. This is a **product ruling**,
not an engineering blocker — both are RENDER-ONLY.

---

## 3. Validated per-surface gap table

Effort: S/M/L/XL (engineering re-size of product's guess). "Class" per §0 legend.

### IA / navigation

| # | Direction A | Corrected Current (`77150e0`) | Class | Effort | GitNexus | Notes |
|---|---|---|---|---|---|---|
| IA-1 | Left sidebar (primary + Operate) + window chrome | 9 flat top tabs (`web/src/routes.tsx`, TopBar) | RENDER-ONLY | L | N/A (frontend) | Shell rebuild; touches every page layout. No backend. |
| IA-2 | Default landing = Home | Default landing = Threads | RENDER-ONLY | S | N/A | One-line route change. |
| IA-3 | Spend dedicated page | Dashboard panel + CLI; no page | RENDER-ONLY (tokens) / NEW-STORE 🚩 (dollars) | L | LOW (read) | Token page is render-only; **dollars = Q1 🚩**. |
| IA-4 | Dreams dedicated surface | **Backend ships**; no web feature folder | RENDER-ONLY (read) + DERIVE/NEW-STORE (see §Dreams) | L | N/A (frontend) | Far less greenfield than draft said. |
| IA-5 | Schedule dedicated surface | **work_hours route + API client ship**; no web folder | RENDER-ONLY (read) | L | N/A (frontend) | **CORRECTION: not blocked/unmerged.** |
| IA-6 | Assistant = omnipresent dock (⌘K) | `/assistant` xterm page (`features/system-assistant`) | RENDER-ONLY + NEW-LOGIC 🚩 (action chips) | L | N/A | Dock is frontend; **chips that execute ops must route through founder gates (P1/safety)**. |
| IA-7 | Settings = page + sub-nav | Modal dialog; no `/settings` route | RENDER-ONLY | M | N/A | Dialog→page; reuse editable Org fields. |
| IA-8 | Jobs tab retired | Dedicated Jobs list + detail ship | RENDER-ONLY (removal) | S–M | N/A | Pure UX removal → **Q6 ruling**. |
| IA-9 | KB → "Knowledge" + folder rail | Tab "KB", flat | RENDER-ONLY | M | N/A | Rename + folder nav (frontend). |
| IA-10 | Nav grouping | Flat | RENDER-ONLY | S | N/A | Cosmetic once IA-1 lands. |

### Home / Dashboard

| Element | Corrected Current | Class | Effort | Notes |
|---|---|---|---|---|
| Calm greeting + Today heartbeat + counters | Two-column board + status strip + heartbeat exist | RENDER-ONLY | M | Frontend reshape. |
| "This week's burn" → links to Spend (tokens) | Token panel on dashboard | RENDER-ONLY | S | Tokens fine. Dollar burn = **Q1 🚩**. |
| Auto-resolution as positive metric ("N cleared by supersede") | `audit_log action='escalation_superseded'` + `RESOLVED_SUPERSEDED` status exist | **DERIVE** | S | Count from existing audit rows; no schema. |
| Tightened escalation triage | Right column text wall | RENDER-ONLY | M | **Q2** ownership. |

### Threads

| Element | Corrected Current | Class | Effort | Notes |
|---|---|---|---|---|
| List w/ last-speaker + avatar + status pills | Subject/chip/turn cards | RENDER-ONLY | M | |
| System/tool-run events visually distinct | **Structured already:** `ThreadMessage.kind` + `system_payload.kind_tag` (`models.py:242-303`, `routes/threads.py`) | RENDER-ONLY | M | **CORRECTION: backed.** *Caveat:* thread system events are dispatch/participant/cap/archive/resume — **in-transcript "tool-run cards"/"ran: cmd" of an agent's own execution are NOT thread messages** (would be NEW-STORE if wanted). |
| Turn cap removal | Turn cap removed from UI per THR-046 msg126; turn count still tracked internally | RENDER-ONLY | S | |
| **@mention routing** | **Pure broadcast — no @mention parsing anywhere** (`routes/threads.py:340-344,372-380`) | **NEW-LOGIC 🚩** | M | **P1 honesty: the UI must NOT imply routing the daemon does not do.** Honest v1 = no @mention affordance, or render as broadcast. Real routing = new daemon logic (founder-gated direction). |

### Tasks

| Element | Corrected Current | Class | Effort | Notes |
|---|---|---|---|---|
| Bidirectional supersede links | **Stored & queryable:** `revisit_of_task_id` + `get_direct_revisits()` (`routes/tasks.py:181-214`) | RENDER-ONLY | S–M | **CORRECTION: backed.** |
| Connected chain timeline, names blocker | `walk_revisit_chain()` exposed; blocked-on data exists | RENDER-ONLY | M | |
| Brief raw markdown + "Show full" | Already exists | RENDER-ONLY (mostly done) | S | |
| Grouping Status/Agent/Thread | Status + team filters | RENDER-ONLY | S | |

### Agents

| Element | Corrected Current | Class | Effort | Notes |
|---|---|---|---|---|
| Editable executor / repos / prompt | **Write routes exist** (`routes/agents.py:349-473,695-746,301-346`); web drawer read-only | RENDER-ONLY (wire-up) | M–L | **CORRECTION: backend exists.** I (EM) may approve wiring; merge/protocol gates must still hold. |
| Accountability metrics (tasks done, accept rate) | No aggregate; derivable — tasks filtered by agent + `audit_log review_verdict` rows | **DERIVE** | M | **P1: must be real counts.** No pre-agg route today; new read/aggregation endpoint, no schema. "Accept rate" = APPROVE/PASS ÷ verdict rows. |
| Inline executor switch | `PUT /agents/{name}/executor` exists | RENDER-ONLY (wire-up) | M | |
| **Autonomy toggle** | **No autonomy field anywhere** in the agent model | **NEW-STORE 🚩 + permission-model 🚩** | M | **Double-flag: new persisted field AND touches the agent permission/autonomy model — both founder-gated. I cannot approve.** |

### Knowledge (KB)

| Element | Corrected Current | Class | Effort | Notes |
|---|---|---|---|---|
| Folder rail nav | Flat KB page (`features/kb`) | RENDER-ONLY | M | Frontend. |
| Dream-candidate review gate (Accept/Edit/Dismiss) | `dream_kb_candidates` table + `status`/`promoted_kb_slug` cols exist; **no mutation route** (`routes/dreams.py` inserts at completion, read-only after) | **DERIVE** | M | Columns exist; needs a `PATCH`/promote route. No schema. Shared with §Dreams. |
| Usage signal ("used by N agents · v3") | `kb_views(view_count,last_viewed_at)` exists, **CLI reads only**; **no per-agent / version counter** | RENDER-ONLY (honest as "viewed N×, CLI") / NEW-STORE 🚩 (for true "N agents"/version) | S | **P1 nuance:** honest signal today is total CLI view count, **not distinct agents and not a version number**. "used by N agents · v3" overstates the store. |
| Citation / "load-bearing" badges | Correctly absent | N/A — cut v1 | — | Needs `kb_consulted` events first (NEW-STORE, already deferred in PRD). |

### Artifacts

| Element | Corrected Current | Class | Effort | Notes |
|---|---|---|---|---|
| 3-col card grid + type filter + provenance | Flat table; name/size/modified exist (`features/artifacts`, `routes/artifacts.py`) | RENDER-ONLY | M | Provenance limited to stored fields. |
| Folder/nested-key browsing | **Backend supports nested keys; web renders flat** (`artifact_store.py:55-110`) | RENDER-ONLY | M | Direction A *also* flat → **Q4**. Backend already supports it if folders are wanted. |
| PR detail: CI/maker-checker/founder-gate checks + files + diff | **No artifact↔PR/CI/job linkage stored at all** | **NEW-STORE 🚩** | L | Substantial new persisted data (artifact→PR/CI/review/job). Founder-gated. |

### Spend (greenfield) — see Q1

| Element | Corrected Current | Class | Effort | Notes |
|---|---|---|---|---|
| Full page: window toggle, fresh-vs-cache, by team/agent/thread/model, top-threads, export | `routes/tokens.py` exposes all of this; **cache separated** (churn invariant intact); CLI exists; no page | RENDER-ONLY (tokens) | L | The token page is honestly buildable now. |
| **Real-dollar cost** anywhere on the page | **Not metered — `$0.00` only** | **NEW-STORE 🚩 (Q1)** | — | Cost meter = price table + `cost_usd` persistence. Founder ruling required. |
| Non-blank model labels | `model` nullable; NULL rows exist | RENDER-ONLY | M | Honest labelling of NULL (e.g. "unknown — pre-fix"); do not fabricate. |

### Dreams (greenfield)

| Element | Corrected Current | Class | Effort | Notes |
|---|---|---|---|---|
| Reflection feed + dream detail (quote/stats/doc/candidates) | **`routes/dreams.py` + `dreams`/`dream_kb_candidates` tables + API client ship**; no web folder | RENDER-ONLY (read) | L | **CORRECTION: backed.** |
| KB-candidate queue w/ confidence + accept flow | Candidates stored; **no Accept/Edit/Dismiss mutation route** | **DERIVE** | M | New route, no schema. |
| Dream-originated threads marked | **No `composed_from_dream_id` on threads**; only dream→thread backref | **NEW-STORE 🚩** | S | New persisted field on threads → founder-gated. |

### Schedule (greenfield) — see correction #1

| Element | Corrected Current | Class | Effort | Notes |
|---|---|---|---|---|
| Overview + per-agent work-hours + "While you were away" + behavior toggles | **`routes/work_hours.py` + API client ship** (`database.py:522-546`); behavior toggles live in Org settings (dreaming/threads schedule) | RENDER-ONLY (read) | L | **CORRECTION: not unmerged/blocked.** Read surface buildable now. |
| **Named recurring wakes** as editable first-class objects | Store records **wake-execution rows**, not editable named-wake **definitions** | **PARTIAL → DERIVE/NEW-STORE 🚩** | M | Listing past/upcoming wakes = render-only; **creating/editing named recurring wakes from UI** likely needs a schedule-config store. Flag for scoping. |

### Assistant (dock)

| Element | Corrected Current | Class | Effort | Notes |
|---|---|---|---|---|
| Omnipresent dock + ⌘K + inline `ran:` transparency | `/assistant` xterm page | RENDER-ONLY | L | Frontend relocation/global state. |
| One-click action chips that execute runtime ops | n/a | **NEW-LOGIC 🚩 (P1/safety)** | M | Any chip that executes privileged ops **must route through the existing founder/job gates** — never bypass approval on merges/protocol edits. |
| Assistant config in Settings | Already in Settings dialog | RENDER-ONLY (placement) | S | |

### Settings (page)

| Element | Corrected Current | Class | Effort | Notes |
|---|---|---|---|---|
| Dedicated page + `/settings` route | Dialog only; no route | RENDER-ONLY | M | |
| Agent-name chips w/ autocomplete | Comma-separated text (`SettingsDialog.tsx`) | RENDER-ONLY | S | |
| Per-field live-vs-restart labels | Partial ("Restart required" badges) | RENDER-ONLY | S | |
| Editable Org (dreaming/threads) | **Already shipped (#102)** | DONE | — | Reuse. |

### Audit

| Element | Corrected Current | Class | Effort | Notes |
|---|---|---|---|---|
| Day-grouped timeline, event-type filter | Activity/Escalations/Traces tabs exist; sidebar filters | RENDER-ONLY | M | |
| Query language + export | None | **DERIVE** (export) / scope query DSL | M | Export = new read route; a query DSL is a larger scoped feature. |
| **Per-event cost** | Not exposed; no per-row cost | **NEW-STORE 🚩 (Q1)** | — | Same cost-meter dependency as Spend. |
| Owns escalation Open/Resolved loop | Served by same store as Home | RENDER-ONLY | — | **Q2** — placement ruling only. |

### Jobs

| Element | Corrected Current | Class | Effort | Notes |
|---|---|---|---|---|
| Job detail: verbatim command/diff + uniform 2-step confirm | `script_text` + interpreter + cwd_hint shown; **no diff** | RENDER-ONLY (command) / NEW-STORE 🚩 (diff) | M | Command transparency is render-only; a stored "diff" preview is not backed. |
| "If approved" cascade | `blocked_on_job_ids` stored + `list_tasks_blocked_on_jobs()`; **no reverse/pre-approval projection** | **DERIVE** | M | **P1: must reflect real downstream tasks.** New query, no schema. |
| No Jobs list (retire tab) | Jobs list + detail ship | RENDER-ONLY (removal) | S–M | **Q6** ruling. |
| No danger tiers | Confirmed: no tier field (only `review_required`) | N/A — cut | — | Correctly absent. |

---

## 4. Tally — render-only vs needs-new-store

Counting the **gap line-items** above (excluding "DONE"/"N/A — cut"):

- **RENDER-ONLY** (frontend only; honest today): **~28** line-items — incl. all of
  IA shell/routing, Threads structured events, Tasks supersede/chain, Agents
  edit-wire-up, KB folder rail, Artifacts grid+folders, Spend **token** page,
  Dreams **read** feed, Schedule **read** surface, Settings page, Audit
  timeline/filter, Jobs detail command. This is the dominant bucket.
- **DERIVE** (new read/aggregation endpoint, **no schema** — within my authority):
  **6** — auto-resolution metric, agent accountability metrics, KB/Dreams
  candidate Accept/Edit/Dismiss route, Jobs "if-approved" cascade projection,
  Audit export, (upcoming-wakes listing).
- **NEW-STORE / NEW-LOGIC 🚩 → FOUNDER ESCALATION:** **8** —
  1. **Real-dollar cost meter** (Spend + "weekly burn" + Audit per-event cost) — **Q1**; price table + `cost_usd` persistence.
  2. **Agent autonomy toggle** — new field **+ permission-model** (double-gated).
  3. **Dream-originated thread marker** (`composed_from_dream_id`).
  4. **Artifact ↔ PR/CI/review/job linkage** (rich PR detail).
  5. **@mention routing** — NEW-LOGIC; daemon is pure broadcast.
  6. **Assistant action chips that execute ops** — NEW-LOGIC/safety; must reuse gates.
  7. **"used by N agents · v3" / citation badges** — needs `kb_consulted`/per-agent + version (already PRD-deferred).
  8. **Editable named-recurring-wake definitions** — likely a schedule-config store (partial; needs scoping).

**No migration is authored in this task.** Each 🚩 above is surfaced for the
founder, per the engineering constraints (a SQLite migration / new persisted field
/ permission-model change is founder-gated; I both cannot and do not design them
in).

---

## 5. Consolidated FOUNDER-DECISION points

Engineering-factual half supplied; product half is product_lead's / founder's.

| # | Decision | Engineering fact (validated) | Type |
|---|---|---|---|
| **Q1** | Spend = tokens or real dollars? | Only tokens stored; `estimated_cost` never populated; `costUSD` discarded. Dollars **require a new cost meter (price table + persisted `cost_usd`)** = migration. Tokens (cache-separated) are honest today. | **FOUNDER 🚩** (schema) |
| **Q2** | Escalation queue: Home vs Audit (vs both)? | **Same store** backs both (`tasks` blocked+escalated / `audit_log` escalation events). Pure UX placement — no data constraint. | Product ruling (no eng blocker) |
| **A1** | Agent **autonomy toggle**? | No autonomy field exists; building it = new persisted field **+** agent permission-model change. | **FOUNDER 🚩** (schema + permission model) |
| **A2** | @mention routing in Threads — honest affordance? | Daemon is **pure broadcast**; no @mention parsing. UI must not imply routing (P1). Real routing = new daemon logic. | **FOUNDER 🚩** (behavioral/orchestrator) |
| **A3** | Assistant action chips that execute ops? | Must route through existing founder/job gates; never bypass merge/protocol approval (P1/safety). | **FOUNDER 🚩** (safety) |
| **A4** | Dream-originated thread marking? | No `composed_from_dream_id` on threads; marking needs a new field. | **FOUNDER 🚩** (schema) |
| **A5** | Artifacts rich PR detail (checks/files/diff)? | No artifact↔PR/CI/job linkage stored; substantial new persisted data. | **FOUNDER 🚩** (schema) |
| **A6** | Spend "real-dollar" — even if built, scope? | (Dependent on Q1.) Cost meter is a separable, founder-gated workstream; read side is LOW blast. | **FOUNDER 🚩** |
| **Q4** | Artifacts folders v1 or flat? | Backend already supports nested keys; web is flat. Either is buildable now (folders = frontend only). | Product ruling |
| **Q6** | Jobs index: retire tab or keep list? | List+detail exist; retiring is pure UX removal. | Product ruling |
| **Q7** | v1 statefulness (click-through vs real approve/save)? | Real save paths exist for Settings/Agents; approve/save can be real where a route exists (else click-through). | Product ruling |
| **K1** | KB "used by N agents · v3" wording? | Store only has total CLI `view_count` (no distinct-agent, no version). Honest = "viewed N× (CLI)"; true claim needs new store. | Product wording / **FOUNDER 🚩** if literal |

**Recommended sequencing (engineering view):** land IA-1 shell + IA-2 routing
first (everything hangs off them); then build the **render-only** surfaces in
parallel — they are now the large majority and carry no data risk (Dreams read,
Schedule read, Spend **token** page, Agents edit wire-up, Tasks/Threads reshapes).
Hold every 🚩 item behind its founder ruling; do not let a prototype's hardcoded
dollar/metric/route imply a store that isn't there (P1).

---

*Validated against `origin/main @ 77150e0`. Authoritative gap call is joint;
engineering-factual half complete. — engineering_manager, TASK-413, 2026-06-16.*
