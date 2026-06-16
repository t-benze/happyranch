# PRD (BUILD-SPEC) — HappyRanch Design Overhaul (Direction A "Pasture")

> **STATUS: AUTHORITATIVE PRODUCT BUILD-SPEC.** This is the document engineering
> scopes the build against. All previously-open product questions are **RULED**
> (founder msgs 62 + 66, THR-010, 2026-06-16). **Scope is locked — do not
> re-litigate decided questions.**
>
> **NOT a roadmap or timeline commitment.** Sequencing notes are engineering
> guidance, not dated commitments; any external timeline still needs separate
> Founder sign-off. No `protocol/` changes are implied by this document.
>
> **Provenance.** Supersedes the `[NEEDS-VALIDATION]` flags in
> `product_lead-2026-06-16-design-overhaul-PRD-draft.md` (TASK-411). Current-state
> ("what's backed today") cells are taken from the **authoritative**
> `engineering_manager-2026-06-16-design-overhaul-gap-analysis-validated.md`
> (TASK-413, validated against `origin/main @ 77150e0`) — **not** from the draft's
> stale first-pass cells. Author: `product_lead`, TASK-415, 2026-06-16.

---

## 0. How to read this build-spec

**The honesty principle (P1) is the dominant acceptance lens.** Every datum on
every surface must trace to a daemon field, audit event, or CLI output. Any
element that would assert something the daemon cannot substantiate is **out of v1
scope**, regardless of how the prototype renders it. Reviewers reject on P1 first,
visuals second.

**v1 scope is defined by what's already backed.** From the EM's validated tally,
v1 = the **~28 RENDER-ONLY** line-items + the **6 DERIVE** line-items (new
read/aggregation routes, **no schema change**, within EM's delegation authority) =
**~34 items**, **plus the single approved schema change A4** (dream-originated
thread marker). Everything that requires a new persisted store, a new daemon
behavior, or a permission-model change is **deferred to the founder-gated post-v1
section (§6)**.

**Class legend** (from the validated gap analysis):
- **RENDER-ONLY** — daemon already stores AND exposes the data via a route; the
  gap is pure frontend. Honest to ship now.
- **DERIVE** — data exists in existing tables but no endpoint yet; needs a new
  read/aggregation route or query. **No schema change.** Honest once computed.
- **DEFERRED 🚩** — needs a new column/table/persisted field, new daemon behavior,
  or a permission-model change → **founder-gated, out of v1** (see §6). The lone
  exception promoted *into* v1 is **A4**.

**Ruling tags.** Each resolved decision is marked **`RULED (2026-06-16)`** inline
where it lands.

---

## 1. Product thesis / north star (unchanged from draft — the "why")

HappyRanch is a **local-first runtime where a solo founder boots and supervises a
"company" of AI agents on their own machine** — a one-operator amplifier. The
redesign is organized around three feeling-words:

1. **Calm / ambient awareness.** Home answers *"is anything on fire?"* and *"what
   needs me?"* **"Calm is the empty queue."** Never manufacture urgency.
2. **Honesty (load-bearing).** Render **only what the orchestration system
   genuinely knows or stores** — no synthesized interpretation, invented danger
   tiers, fabricated clusters, or unmeasurable progress. **This is P1, the single
   most important acceptance lens.**
3. **Lower density, modern operator feel** (Claude/Codex/Slack-like), destined to
   be embedded as a webview in a native macOS/Windows shell, built by engineers
   and released to end users.

"Honesty" and "calm" are **cut criteria**, not decoration.

---

## 2. Cross-cutting principles & constraints (apply to every surface)

| # | Principle | Acceptance implication |
|---|---|---|
| P1 | **Render only system-stored facts.** No synthesized interpretation. | Every datum traces to a daemon field, audit event, or CLI output. **Dominant acceptance lens.** Reject any "computed insight" with no backing store. |
| P2 | **Calm by default.** Surface attention, never manufacture it. | Default/empty/quiet states read as *intentional*. "Quiet dream", "Failure 0", "nothing needs you" are first-class positive states. |
| P3 | **One owner per capability.** Home = "is anything on fire" / "what needs me now". Audit = forensic record + resolved history. Spend = token burn. | No surface duplicates another's job. |
| P4 | **Left sidebar nav + desktop window chrome**, retiring the ~9 top-tab bar. | Grouped left rail; shell carries window controls for the native wrapper. |
| P5 | **Theme toggleable and persists** across navigation (light-first "Pasture"; dark supported). | Both themes first-class; persists via storage. |
| P6 | **Consistent object/ID taxonomy with click-through.** `THR-`/`TASK-`/`JOB-`/`PR #`/agent/executor. | Every ID is a link; provenance is first-class metadata, limited to **stored** fields. |
| P7 | **Cost transparency, surfaced honestly.** | Token "churn" = `total = input + output + reasoning` only; **cache reads in a separate column, never folded into churn or used as a ranking key** (KB `token-usage-surface-ownership-doctrine`). **Dollars = `$0.00` / "not metered" in v1** (Q1 ruling). |

---

## 3. Information Architecture (the heart of Direction A)

### 3.1 Direction A nav (RULED, RENDER-ONLY)

- **Primary group:** `Home · Threads · Tasks · Agents · Knowledge · Artifacts`
- **"Operate" group:** `Spend · Dreams · Schedule · Audit`
- **Footer:** `Settings` (+ founder identity block, theme toggle, org switcher)
- **Assistant is NOT a tab** — it is an **omnipresent dock** (⌘K / "Ask or
  search" pill). See §4.10.
- **Jobs is NOT a tab** — retired. Jobs reached contextually + rolled up on Home +
  history in Audit (Q6 ruling; see §4.13).
- **Default landing = Home** (was Threads).

### 3.2 IA deltas — v1 disposition

| # | Move | Class | v1? | Notes |
|---|---|---|---|---|
| IA-1 | Left sidebar (primary + Operate) + window chrome | RENDER-ONLY (L) | **v1** | Shell rebuild; touches every page layout. **Build first** (§7). |
| IA-2 | Default landing = Home | RENDER-ONLY (S) | **v1** | One-line route change. **Build first** (§7). |
| IA-3 | Spend dedicated page (tokens) | RENDER-ONLY (L) | **v1** | Dollars deferred (Q1 → §6). |
| IA-4 | Dreams dedicated surface (read) | RENDER-ONLY (L) | **v1** | Backend ships; far less greenfield than first thought. |
| IA-5 | Schedule dedicated surface (read) | RENDER-ONLY (L) | **v1** | **Correction: not blocked/unmerged** — `work_hours` route + API client ship. |
| IA-6 | Assistant = omnipresent dock (⌘K) | RENDER-ONLY | **v1** | Action chips gated (A3); build approach per TASK-414. |
| IA-7 | Settings = page + sub-nav | RENDER-ONLY (M) | **v1** | Dialog → page; reuse editable Org fields. |
| IA-8 | Jobs tab retired | RENDER-ONLY removal (S–M) | **v1** | Q6 RULED. |
| IA-9 | KB → "Knowledge" + folder rail | RENDER-ONLY (M) | **v1** | Rename + folder nav (frontend). |
| IA-10 | Nav grouping (primary / Operate) | RENDER-ONLY (S) | **v1** | Cosmetic once IA-1 lands. |

> **Open IA refinement (NON-BLOCKING, not ruled):** whether to eventually **unify
> Agents + Settings** into one admin surface (draft Q5) was *not* ruled in msgs
> 62/66. It is **not required for v1** — both surfaces ship separately (the
> Direction A prototype default) and both are RENDER-ONLY. Treat unification as a
> post-v1 IA question; it blocks nothing. Flagged for Founder awareness, not for
> resolution before build.

---

## 4. Per-surface build-spec

Each surface lists **Purpose · v1 scope (what ships) · Deferred · Acceptance
criteria** with P1 as the dominant lens. Ordered by the Direction A sidebar.

---

### 4.1 Home (Dashboard) — RENDER-ONLY + 1 DERIVE

- **Purpose.** The calm landing/triage surface: "is anything on fire, and what
  wants me right now?" in one look.
- **v1 scope.**
  - Narrative greeting in plain language ("Two things need you, the rest is
    humming").
  - **"Today" heartbeat** — 24-bar hourly sparkline (quiet hours dimmed) +
    counters: Completed / Failed / Active now / KB entries / Spend today.
  - **"This week's burn" glance card in TOKENS**, linking into Spend (P3). Dollars
    render `$0.00 / not metered` (Q1).
  - **Org pulse** per-team 7-day acceptance table.
  - **Auto-resolution as a calm positive metric** ("6 escalations cleared by
    supersede this week") — **DERIVE** (count from existing `audit_log
    action='escalation_superseded'` rows; no schema). Closes the "invisible
    auto-resolution" gap.
  - **Active escalation triage list lives HERE.** **RULED (2026-06-16) — Q2: HOME
    owns active triage; AUDIT owns the resolved / Open→Resolved history.** Home
    shows the tightened "what needs you now" queue (kind→verb action mapping,
    2-line clamp, demoted long tail); the resolved-escalation history is Audit's.
  - **Jobs "awaiting you" rollup.** **RULED (2026-06-16) — Q6:** Home shows only
    the *awaiting-you* job rollup (a count/list that links to each job's
    contextual location); it does **not** reproduce a Jobs index, and completed
    jobs are not shown here (they live in Audit).
- **Deferred.** Dollar "burn" figure (Q1 → §6); any forecast/interpreted value (P1).
- **Acceptance criteria.**
  - AC1: Default landing route resolves to **Home**, not Threads. **[verifiable]**
  - AC2 (P1): Every number on Home traces to a stored daemon fact — no
    interpreted/forecast values. **[verifiable]**
  - AC3: The token-burn figure on Home equals the Spend page's same-window figure
    (single source of truth; P3). **[verifiable]**
  - AC4: The active-escalation queue on Home reads the **same store** as Audit's
    resolved history; an item appears in exactly one place per its Open/Resolved
    state. **[verifiable]**
  - AC5 (dogfood): A founder returning after N hours can name the count of items
    needing them and open the first one in ≤2 clicks. **[usability target]**

---

### 4.2 Threads (list + detail) — RENDER-ONLY

- **Purpose.** Founder-visible, multi-agent **broadcast** conversations for
  coordination and cross-team handoff. The sole collaboration surface.
- **v1 scope.**
  - **List:** segmented filter (`All / Waiting on you / Active / Done`) with
    counts; each row leads with the **last speaker**; overlapping avatar stack for
    multi-agent participation; unread tint; status pills (`waiting on you` /
    `active` / `review` / `merged` / `idle`) + green `live` pill.
  - **Detail:** 2-col — scrolling transcript + 300px rail (Participants / Linked
    tasks / Artifacts / this-thread stats: messages, **token churn**, opened).
  - **System/execution events visually distinct from prose** — backed by
    `ThreadMessage.kind` (MESSAGE/DECLINE/SYSTEM) + `system_payload.kind_tag`
    (RENDER-ONLY). **Scope nuance (P1):** the backed system events are
    *dispatch / participant / cap / archive / resume*. In-transcript **"ran:
    `<cmd>`" cards representing an agent's *own* execution are NOT thread messages
    today** — do **not** synthesize them into the thread transcript in v1 (that
    would be a new store; deferred). The assistant's own `ran:` transparency cards
    live in the **Assistant dock** (§4.10), where they are real.
  - **@mention rendered BROADCAST-ONLY.** **RULED (2026-06-16) — A2:** the daemon
    is pure broadcast; there is **no @mention routing**. The composer may accept
    text, but **no affordance may imply an @mention changes routing or wakes a
    specific agent** (P1 honesty). Render @mentions as plain broadcast references,
    not as a routing control.
  - **Turn budget (X/500)** visible before the cap.
- **Deferred.** Real @mention routing (NEW-LOGIC → §6); agent's-own-execution
  tool-run cards inside the thread transcript (new store → §6).
- **Acceptance criteria.**
  - AC1: From the list, the founder can tell *who spoke last* and *whether it needs
    them* without opening the thread. **[verifiable]**
  - AC2: System/dispatch events are visually distinct from human/agent prose.
    **[verifiable]**
  - AC3: Turn budget (X/500) is visible before the cap. **[verifiable]**
  - AC4 (P1): No UI element implies @mention routing the daemon does not perform.
    **[verifiable — dominant lens here]**

---

### 4.3 Tasks (list + detail) — RENDER-ONLY

- **Purpose.** The org-wide work board (a **list**, not a kanban) + a per-task
  decision/lineage surface.
- **v1 scope.**
  - **List axis = roots-only by default (parent→subtask execution tree).** The list
    shows **one row per root task**; execution subtasks (`task_type='subtask'` /
    `parent_task_id`) are **not** their own rows. A **severity-max rollup** on the
    root row reflects the **worst status among its descendants** — a blocked/failed
    subtask lights up its root, so nothing hides. **Subtasks are always shown in the
    task DETAIL view** (the chain/lineage timeline). **No in-list "show subtasks"
    toggle in v1** (founder ruling THR-010 msg 100; product_lead scope msg 101/104 —
    an opt-in in-list full-visibility toggle stays a deferred optional, added only if
    the founder later asks). **ADDITIVE, parent/subtask axis only** — the
    revisit/supersede chain lineage (`revisit_of_task_id` / `walk_revisit_chain()`)
    is **UNCHANGED** by this. **RENDER-ONLY/DERIVE:** `parent_task_id` + `task_type`
    already exist; the rollup is a derive over existing child statuses; **no schema
    change**.
  - **List:** dense 44px one-line rows; group-by `Status / Agent / Thread`; status
    groups including **Resolved (superseded)** (dimmed, retained). **Bidirectional
    lineage inline** — `↳ supersedes TASK-381` (forward) and `→ TASK-407`
    (back-pointer) — backed by `revisit_of_task_id` + `get_direct_revisits()`
    (RENDER-ONLY; corrected from draft's "needs storing").
  - **Detail:** **connected vertical chain timeline** (`walk_revisit_chain()`
    exposed) with node states (done / current-with-glowing-ring / blocked); a
    blocked node **names its blocker** ("waits on TASK-349"); property rail
    (assignee, executor, thread, job, churn, created, priority); append-style
    activity log; contextual primary action.
  - **Brief renders as raw monospace markdown with a "Show full" toggle** — no
    synthesized slot parsing (P1; the parser does not exist; mostly already done).
- **Deferred.** None — all v1.
- **Acceptance criteria.**
  - AC1: List is a list, not a board. **[verifiable]**
  - AC2: From a superseded task the founder reaches its successor in 1 click, and
    vice-versa. **[verifiable]**
  - AC3 (P1): The brief is raw markdown; no invented "N-slot" parsing.
    **[verifiable]**
  - AC4: A blocked task always shows *what it is blocked on* (real `blocked_on`
    data). **[verifiable]**
  - AC5: The list shows **roots only** — no execution subtask (`task_type='subtask'`
    / `parent_task_id`) appears as its own row; subtasks are reachable in the task
    **detail** chain timeline. **[verifiable]**
  - AC6: A root with a blocked/failed descendant surfaces that **worst** child
    status on its own row (severity-max rollup) — urgency never hides behind the
    roots-only filter. **[verifiable]**

---

### 4.4 Agents — RENDER-ONLY (wire-up) + 1 DERIVE

- **Purpose.** Editable agent roster + rich detail.
- **v1 scope.**
  - Two-pane (roster list + roomy editable detail — "not a cramped drawer").
  - **Editable system prompt, executor switch (segmented `codex / claude / pi`),
    team, repo chips (add/remove), tool chips** — all **RENDER-ONLY wire-up**: the
    write routes already ship (`POST /agents/manage`, `PUT /agents/{name}/executor`,
    `POST /agents/{name}/repos`); the drawer is just read-only today. **These are
    REAL saves in v1 (Q7).**
  - **Accountability metrics on the agent** ("42 tasks done · 88% accept rate") —
    **DERIVE** (new read/aggregation endpoint; tasks filtered by agent +
    `audit_log review_verdict` rows; "accept rate" = APPROVE/PASS ÷ verdict rows).
    **No schema.** Must be **real counts** (P1) — never estimates.
  - "Edits take effect on this agent's next task."
- **RULED (2026-06-16) — A1: the "Can act autonomously" per-agent toggle is
  DEFERRED, out of v1** (NEW-STORE + permission-model → §6). Do **not** render a
  non-functional autonomy toggle.
- **Deferred.** Autonomy toggle (A1 → §6).
- **Acceptance criteria.**
  - AC1: Founder can change an agent's executor / repos / system prompt from the UI
    and it **persists** (real route; Q7). **[verifiable]**
  - AC2 (P1): Accountability metrics are real stored/derived counts, not estimates.
    **[verifiable]**
  - AC3: No autonomy toggle is shown in v1. **[verifiable]**
  - AC4: An edited system prompt is what the agent's next session receives.
    **[verifiable]**

---

### 4.5 Knowledge (KB) — RENDER-ONLY + 1 DERIVE

- **Purpose.** The org knowledge library, browsable by folder, with a
  dream-candidate review gate.
- **v1 scope.**
  - **List:** folder rail (`All entries`, `Engineering > review/qa/build`,
    `Org > protocols / from dreams`) + stacked entry feed. Dream-proposed entries
    **visually distinct** (accent moon glyph) and quarantined as "pending review".
  - **Usage signal label = "viewed N× (CLI)".** **RULED (2026-06-16) — K1:** the
    store has only a **total CLI view count** (`kb_views.view_count`); there is
    **no distinct-agent counter and no version number**. Render **"viewed N×
    (CLI)"** only. **Drop "used by N agents" and "v3"** — they overstate the store
    (P1). (Per KB `kb-view-tracking-caller-signal`: the daemon cannot distinguish
    distinct agents on one shared token.)
  - **Detail:** fully-rendered doc with a **sticky candidate banner (Accept /
    Edit-first / Dismiss)** for dream candidates — **DERIVE** (the
    `dream_kb_candidates` table + `status`/`promoted_kb_slug` columns exist; needs
    a new `PATCH`/promote mutation route; **no schema**). Shared flow with §4.8.
    **Real mutation in v1 (Q7).**
- **Deferred.** Citation / "load-bearing"/"uncited" badges (needs `kb_consulted`/
  `kb_referenced` events — NEW-STORE → §6); true distinct-agent / version usage
  signal (NEW-STORE → §6).
- **Acceptance criteria.**
  - AC1: A dream candidate can be Accepted / Edited / Dismissed from the entry
    view, and Accept makes it live (real route). **[verifiable]**
  - AC2 (P1): Usage label reads "viewed N× (CLI)" — no "N agents", no version.
    **[verifiable]**
  - AC3: No citation/"load-bearing" badge ships in v1. **[verifiable]**

---

### 4.6 Artifacts — RENDER-ONLY

- **Purpose.** The gallery of everything agents produced, tied back to the
  producing thread/agent.
- **v1 scope.**
  - **FLAT recency card grid.** **RULED (2026-06-16) — Q4: ship the flat grid for
    v1; folder browsing is DEFERRED** (the backend already supports nested keys, so
    it is revisitable later without a migration — but it is out of v1 scope).
  - 3-col card grid with type filter (`All / Pull requests / Docs / Patches /
    Designs`); each tile carries a kind pill, status tag (`merged / final /
    applied / open / draft / v2`), and **provenance** (producing thread + agent +
    age) — limited to **stored** fields (P1).
- **Deferred.** Folder/nested-key browsing (Q4 — buildable later, frontend-only);
  **rich PR detail** (CI/maker-checker/founder-gate checks + files-changed + diff)
  — **RULED (2026-06-16) — A5: DEFERRED** (no artifact↔PR/CI/review/job linkage
  stored; substantial NEW-STORE → §6).
- **Acceptance criteria.**
  - AC1: Every artifact shows which thread + agent produced it (from stored
    fields). **[verifiable]**
  - AC2 (P1): No PR "checks" panel renders fabricated/un-stored check states in v1.
    **[verifiable]**
  - AC3: Artifacts list is a flat grid; no folder tree in v1. **[verifiable]**

---

### 4.7 Spend (homeless → home) — RENDER-ONLY (tokens)

- **Purpose.** The **single owner** of token observability; reconciles tokens ↔
  cache; resolves cryptic model labels.
- **v1 scope.**
  - **TOKENS-ONLY.** **RULED (2026-06-16) — Q1:** the daemon stores token counts
    only; `estimated_cost` is never populated and `costUSD` is discarded by the
    executor parser. **Spend = tokens as the budget unit.** Render dollars as
    **`$0.00` / "not metered"** — never let a prototype's hardcoded dollar figure
    ship as if real (P1).
  - Hero "this week's burn" card (window toggle **24h / 7d / 30d**, 7d default)
    with a **fresh-vs-cache split**; "where it went" breakdown by **team & agent**,
    by **thread**, by **model** (segmented `Thread / Agent / Model`); "Top threads
    by churn" table; Export. (`routes/tokens.py` exposes all of this — RENDER-ONLY.)
  - **Churn invariant [BINDING — KB `token-usage-surface-ownership-doctrine`].**
    `total = input + output + reasoning`. **Cache reads render in a separate
    column and are NEVER folded into churn or used as a ranking key.**
  - **Model-label honesty.** Cryptic/NULL labels (`(unknown — pre-fix)`,
    `(unknown — ANOMALY)`, `(mixed)`, `(cli-unreported)`) render as **labeled**
    values — never blank, never silently "corrected" into a wrong model (O1–O4).
  - **Cache savings framed as the hero virtue** ("Cache saved 241M tokens · 57% of
    reads served from cache").
- **Deferred.** Any real-dollar figure → the **cost meter** (price table +
  persisted `cost_usd`) is a separate founder-gated workstream (Q1/A6 → §6).
- **Acceptance criteria.**
  - AC1 (P1, binding): Cache reads never appear inside the churn/total number.
    **[verifiable]**
  - AC2 (P1): Every model label is non-blank and never a fabricated correction.
    **[verifiable]**
  - AC3 (P1): Dollars render as `$0.00 / not metered` consistently across Home,
    Spend, Threads, and Audit — no surface shows a fabricated dollar figure.
    **[verifiable]**

---

### 4.8 Dreams (homeless → home) — RENDER-ONLY (read) + 1 DERIVE + **A4 (the one v1 schema change)**

- **Purpose.** First-class **nightly-reflection** surface: agents reflect off the
  task clock, write learnings, propose KB candidates, open founder threads only
  when output is worth attention.
- **v1 scope.**
  - **Reflection feed + dream detail** (quote → stat strip → narrative doc →
    proposed-knowledge cards → "Open reflection thread") — **RENDER-ONLY**:
    `routes/dreams.py` + `dreams`/`dream_kb_candidates` tables + API client ship
    (corrected from draft's "greenfield").
  - Reflections rendered as **human, narrative pull-quotes** — the agent's actual
    stored reflection text, not a UI-invented summary (P1).
  - **KB-candidate review queue (Accept / Edit-first / Dismiss)** — **DERIVE**
    (shared mutation route with §4.5; columns exist; no schema). Real in v1 (Q7).
  - **Dream-originated thread marker — IN v1.** **RULED (2026-06-16) — A4: DO IT.**
    Add an **additive, nullable `composed_from_dream_id` column on threads** — the
    **single approved schema change** in this overhaul. This closes the "a
    dream-thread is indistinguishable from a coordination thread" gap: a thread
    opened by a dream is marked as dream-originated everywhere it appears (Threads
    list/detail, Home, Audit). *Engineering note: additive nullable column, no
    backfill required for existing rows.*
  - **Quiet dream** ("Quiet dream — nothing escalated · private learning saved")
    is an explicit non-alarming valid state (P2).
- **Deferred.** None of Dreams' v1 elements are deferred (A4 is *in*).
- **Acceptance criteria.**
  - AC1: A dream is visually distinguishable from a coordination thread everywhere
    it appears, **driven by `composed_from_dream_id`** (A4), not by a UI guess.
    **[verifiable]**
  - AC2: Founder can Accept/Edit/Dismiss a KB candidate from Dreams or Knowledge
    and the result is consistent (shared real route). **[verifiable]**
  - AC3 (P1): Reflection text shown is the agent's actual stored reflection.
    **[verifiable]**

---

### 4.9 Schedule (homeless → home) — RENDER-ONLY (read)

- **Purpose.** Give agents a working-day rhythm; make unattended scheduling
  visible and trustworthy.
- **v1 scope.**
  - **Read surface is buildable now** — **correction from draft:** `work_hours`
    route + `web/src/lib/api/work-hours.ts` ship on `main`; **not blocked/unmerged**.
  - Overview (week grid with working-hour + dream bands, or per-agent 24h timeline
    with a "now" line and scheduled-wake dots) + **per-agent work hours** +
    **listing of past/upcoming wakes** + a **"While you were away"** wake feed.
  - **Behavior toggles** (Finish in-flight work / Hold escalations / Urgent
    override) — surfaced from the **existing Org settings** (dreaming/threads
    schedule), which already ship.
- **Deferred.** **Creating/editing named recurring "wakes" as first-class editable
  objects** — the store records wake-*execution* rows, not editable named-wake
  *definitions*; an editor likely needs a schedule-config store (PARTIAL → NEW-STORE
  → §6). v1 **lists** wakes; it does not let you author new named recurring wakes
  from the UI.
- **Acceptance criteria.**
  - AC1: Founder can **view** per-agent work-hours and past/upcoming wakes from the
    UI (read parity with the `work-hours` CLI). **[verifiable]**
  - AC2 (P1): Schedule reflects the daemon's actual scheduler state, not a UI mock.
    **[verifiable]**
  - AC3: No UI affordance implies you can create a new named recurring wake in v1
    unless/until the schedule-config store is ruled in. **[verifiable]**

---

### 4.10 Assistant (dock, not a page) — RENDER-ONLY + gated chips

- **Purpose.** A **conversational operator** that both answers and acts on the
  runtime — "an assistant that runs your runtime, not a terminal."
- **PRODUCT INTENT (RULED — A3, 2026-06-16).**
  - The Assistant is a **conversational DOCK**: **omnipresent**, opened via **⌘K**
    / the "Ask or search" pill (Esc closes); persists across navigation.
  - **Inline "ran: `<cmd>`" transparency cards** — every command the assistant runs
    is shown verbatim (churn/cache/model where relevant). This is a transparency
    affordance and is **real** (the dock executes real commands).
  - **One-click action chips** ("Approve JOB-083", "Open THR-021", "Show the diff")
    — **chips are offered ONLY for operations that are already gated, and every chip
    routes through the EXISTING founder/job gate. A chip NEVER bypasses merge or
    protocol-edit approval** (P1/safety, NEW-LOGIC constraint). A chip that triggers
    a privileged op surfaces the standard approval, it does not auto-execute.
  - The current `/assistant` **xterm becomes the "Open full session" escape
    hatch** in the dock header — the raw terminal is retained behind that link, not
    as a standalone page.
- **BUILD APPROACH — see TASK-414.** Whether the dock is implemented **on top of
  the existing xterm** or as a **separate React component** is being determined by
  **EM's feasibility spike TASK-414**. This PRD specifies **product intent only**;
  **the implementation path is TASK-414's call — do not prescribe it here.**
- **Deferred.** Any action chip whose underlying op is *not* already gated, or any
  chip that would execute a privileged op without routing through the existing gate
  (NEW-LOGIC/safety → §6 if ever proposed).
- **Acceptance criteria.**
  - AC1: ⌘K opens the dock from every surface; Esc closes; state persists.
    **[verifiable]**
  - AC2 (P1): Any command the assistant runs is shown verbatim. **[verifiable]**
  - AC3 (P1/safety): An assistant action requiring founder approval routes through
    the standard gate; it never auto-executes a protocol edit / merge.
    **[verifiable]**
  - AC4: "Open full session" reaches the retained xterm terminal. **[verifiable]**

---

### 4.11 Settings (page, not dialog) — RENDER-ONLY

- **Purpose.** The in-app configuration surface.
- **v1 scope.**
  - Dedicated **page** with sticky left sub-nav (`Assistant · System ·
    Organization · Agents · Executors · Billing`) + field panel + sticky save bar;
    a real bookmarkable **`/settings` route**.
  - **Org section editable** (Nightly dreaming: enabled / schedule / timezone /
    agent mode / catch-up; Threads: default turn cap / session timeout) — **already
    shipped (#102)**; reuse. **Real saves in v1 (Q7).**
  - **Per-field live-vs-restart labeling** ("Applies immediately" vs "restart to
    apply"), matching actual daemon behavior (P1).
  - **Agent-name chips with autocomplete** replacing comma-separated text fields.
  - System Assistant config (status, Init/Repair, "Open terminal") lives here,
    integrating §4.10.
- **Deferred.** Unifying Agents + Settings into one admin surface (non-blocking,
  not ruled — see §3.2 note); Billing sub-nav remains tokens-only until the cost
  meter is ruled in (Q1 → §6).
- **Acceptance criteria.**
  - AC1: `/settings` is a real bookmarkable route. **[verifiable]**
  - AC2 (P1): Every field is correctly labeled live-apply vs restart-required,
    matching real daemon behavior. **[verifiable]**
  - AC3: Agent-name inputs autocomplete from the real roster (no free-text typos).
    **[verifiable]**

---

### 4.12 Audit — RENDER-ONLY + 1 DERIVE

- **Purpose.** The immutable, append-only forensic record — "what happened, who,
  when" — exportable. **Owner of the resolved-escalation history.**
- **v1 scope.**
  - Day-grouped timeline; color-coded event classes (completed / merge /
    escalation / failure); every entry carries **executor + token cost (tokens,
    not dollars)**; an **Event-types legend with counts** that doubles as a filter;
    Export.
  - **Owns the resolved escalation history.** **RULED (2026-06-16) — Q2: Audit owns
    the resolved / Open→Resolved escalation history**; Home owns the *active*
    triage. Both read the **same store** (`tasks` blocked+escalated /
    `audit_log` escalation events) — placement is the only difference.
  - **Owns completed/past jobs.** **RULED (2026-06-16) — Q6:** completed and past
    jobs live in **Audit (history)**; Home shows only the awaiting-you rollup; live
    job detail is reached **contextually** from the spawning thread/task. The
    standalone Jobs **tab is retired**.
  - **Export = DERIVE** (new read route; no schema).
  - **"Failure 0" is a first-class calm state** (P2).
- **Deferred.** **Per-event real-dollar cost** (same cost-meter dependency as
  Spend — Q1 → §6); a full **query DSL** (`actor:dev_agent action:merge since:7d`)
  is a larger scoped feature — v1 ships the legend-as-filter; the mono query
  language is **deferred** unless EM scopes it within v1.
- **Acceptance criteria.**
  - AC1: Every audit line is immutable, attributable (actor), and exportable.
    **[verifiable]**
  - AC2: Filtering by event type / actor / time maps to stored events (P1).
    **[verifiable]**
  - AC3: Resolved escalations and completed jobs appear in Audit; active
    escalations appear on Home; no item is double-owned (Q2/Q6). **[verifiable]**

---

### 4.13 Jobs — NO standalone surface (retired); RENDER-ONLY job *detail* + 1 DERIVE

- **RULED (2026-06-16) — Q6.** The standalone **Jobs tab is retired.** Jobs are:
  - **rolled up on Home** as the "awaiting you" list (§4.1),
  - **historical in Audit** (completed/past jobs; §4.12),
  - **reachable contextually** from the spawning thread/task (the job detail's
    breadcrumb is "Back to THR-021" — jobs are thread/task-anchored).
- **Job detail (v1 scope).**
  - **Verbatim command** (`script_text` + interpreter + cwd_hint) — RENDER-ONLY.
  - **"If approved" cascade** ("protocol updated → PR #101 unblocks → TASK-351
    becomes runnable") — **DERIVE** (`blocked_on_job_ids` +
    `list_tasks_blocked_on_jobs()`; a new forward-projection query; no schema).
    **P1: must reflect real downstream tasks** — no invented effects.
  - **Honest attention signal + uniform two-step confirm.** "🔑 needs credential" /
    "flagged for review" with a **uniform confirm — NO danger-tier ranking** (the
    system can't rank danger; tiers were explicitly rejected — only `review_required`
    exists).
  - **Real approval in v1 (Q7)** where the route exists.
- **Deferred.** A **stored diff preview** in job detail (no diff is stored today —
  NEW-STORE → §6); any Jobs *index/list* page (retired by Q6).
- **Acceptance criteria.**
  - AC1: Job approval shows the real command and a real downstream-impact list
    (no invented effects — P1). **[verifiable]**
  - AC2: No danger-tier ranking; uniform two-step confirm. **[verifiable]**
  - AC3: No standalone Jobs tab/index exists; jobs are reachable via Home rollup,
    Audit history, and thread/task context. **[verifiable]**

---

## 5. The "no" list (cuts — re-proposing needs a Founder reversal)

| Cut | Why (honesty / scope) |
|---|---|
| Jobs **danger tiers**, target chips, progress bars, "Effect" line, dry-run preview | Not derivable from stored state (P1). |
| Job detail **stored diff** preview | No diff is stored (NEW-STORE; deferred §6). |
| Agent **failure-pattern psychoanalysis** + prefab recommendations + invented clusters | Synthesized interpretation (P1). |
| KB **citation badges** / "load-bearing"/"uncited" sort | No backing audit event yet (P1; deferred §6). |
| KB **"used by N agents · v3"** | Store has only total CLI view count; overstates the store (P1; K1 → "viewed N× (CLI)"). |
| Tasks **board/kanban** view | User explicitly chose list. |
| Tasks **8-slot brief parser** | No parser exists; raw markdown + "Show full". |
| **Real-dollar figures** on any v1 surface | Not metered; `$0.00 / not metered` only (Q1). |
| **@mention routing** affordance | Daemon is pure broadcast; UI must not imply routing (A2/P1). |
| Agent **autonomy toggle** (rendered as functional) | No field; permission-model change (A1; deferred §6). |
| In-transcript **agent-own-execution "ran:" cards** in Threads | Not thread messages; would be a new store (deferred §6). The dock's `ran:` cards are real. |
| Direction B **"Mission Control"** | Pasture/A won. |
| Reintroducing **Talks** | Removed (PR #103); superseded. |

---

## 6. Deferred / post-v1 (FOUNDER-GATED)

These are **explicitly out of v1**, captured here so they are not lost. Each
requires a new persisted store, new daemon behavior, or a permission-model change
→ founder ruling + (for schema) a SQLite migration that engineering will not author
without sign-off. **None block the v1 render-only/derive build.**

| # | Deferred item | Why gated | Unblocks |
|---|---|---|---|
| D1 | **Real-dollar cost meter** (Spend dollars, Home dollar burn, Audit per-event cost, Settings → Billing) | NEW-STORE: per-model price table + cost computed at capture-time + persisted `cost_usd` (or populate `task_results.estimated_cost`). Executors are flat-rate local CLIs; `costUSD` is currently discarded. **(Q1 / A6)** | Dollar framing across all cost surfaces. |
| D2 | **Agent autonomy toggle** ("can act autonomously / skip approval for low-risk") | NEW-STORE **+ permission-model**: no autonomy field exists; double-gated (schema + changes the approval/permission model). **(A1)** | Per-agent autonomy in Agents §4.4. |
| D3 | **@mention routing** in Threads | NEW-LOGIC: daemon is pure broadcast; real routing changes orchestration semantics. Until then, broadcast-only (A2). | Directed thread messaging. |
| D4 | **Artifacts ↔ PR/CI/review/job linkage** (rich PR detail: checks + files-changed + diff) | NEW-STORE: substantial new persisted linkage; none stored today. **(A5)** | Artifacts §4.6 PR detail. |
| D5 | **KB rich usage + citation badges** ("used by N distinct agents", version number, "load-bearing"/cited-by) | NEW-STORE: needs per-agent attribution + version counter + `kb_consulted`/`kb_referenced` events. Until then, "viewed N× (CLI)" (K1). | Knowledge §4.5 trust signals. |
| D6 | **Editable named-recurring-wake definitions** (author new wakes from the Schedule UI) | NEW-STORE (likely a schedule-config store): the store records wake-*execution* rows, not editable wake *definitions*. **Needs EM scoping.** | Schedule §4.9 authoring. |
| D7 | **In-thread agent-own-execution "ran:" cards** | NEW-STORE: these are not thread messages today. (The Assistant dock's `ran:` cards are real and shipped in v1.) | Threads §4.2 richer transcript. |
| D8 | **Job detail stored diff preview** | NEW-STORE: no diff is persisted; only `script_text`/interpreter/cwd_hint. | Jobs §4.13 diff. |
| D9 | **Audit query DSL** (`actor: action: since:`) | Larger scoped feature beyond the legend-as-filter; defer unless EM scopes into v1. | Audit §4.12 power search. |

> **The one schema change promoted INTO v1:** **A4 — `composed_from_dream_id`**
> (additive, nullable column on threads). Approved by the founder as the single v1
> migration because it closes the load-bearing "dream-thread indistinguishable from
> coordination" honesty gap and is low-risk (additive, nullable, no backfill).

---

## 7. Honesty-tier sequencing (engineering alignment — NOT a timeline)

Aligns with the EM's recommended sequencing. **This is dependency ordering, not a
dated commitment** (timeline = separate Founder sign-off).

1. **Foundation first — IA-1 shell + IA-2 routing.** Everything hangs off the
   sidebar shell + window chrome (IA-1) and the Home-default route (IA-2). Build
   these first; every surface re-parents into the new shell.
2. **Then the render-only surfaces in parallel** — now the large majority, carrying
   **no data risk**: Dreams (read), Schedule (read), Spend (token page), Agents
   edit wire-up, Tasks/Threads reshapes, Knowledge folder rail, Artifacts flat
   grid, Settings page, Audit timeline/filter, Jobs detail + retire-tab.
3. **DERIVE items alongside** (each is a new read/aggregation route, no schema, in
   EM's authority): auto-resolution metric, agent accountability metrics,
   KB/Dreams candidate Accept/Edit/Dismiss route, Jobs "if-approved" cascade,
   Audit export, upcoming-wakes listing.
4. **A4 (`composed_from_dream_id`)** — the single v1 migration; sequence with the
   Dreams + Threads work that consumes the marker.
5. **Hold every §6 item behind its founder ruling.** Do not let a prototype's
   hardcoded dollar/metric/route imply a store that isn't there (P1).

---

## 8. Measurable success criteria (org-level)

Tied to founder value, not output:
- **Calm:** founder answers "is anything on fire?" in one glance on Home without
  opening Audit. (Dogfood.)
- **Homes for the homeless:** Spend, Dreams, Schedule each reachable in 1 click
  from the sidebar; the "indistinguishable dream-thread" gap is closed by A4.
- **Honesty (dominant):** zero UI elements assert facts the daemon can't
  substantiate; dollars render `$0.00 / not metered`; @mention is broadcast-only;
  KB shows "viewed N× (CLI)". (Review gate enforces P1.)
- **Cost legibility:** founder answers "what did this week cost (in tokens), and on
  what?" on one surface (Spend), with one consistent token model across surfaces.
- **Time-to-decision:** a founder-gated approval (job/PR/protocol) is reachable and
  actionable in ≤2 clicks from Home. (Dogfood.)
- **Statefulness (Q7):** approvals and edits are **real saves** wherever a route
  exists (Settings, Agents, KB/Dreams candidates, job approval) — no fake
  click-through on backed paths.

---

*End of build-spec. Inputs: `product_lead-2026-06-16-design-overhaul-PRD-draft.md`
(TASK-411) + `engineering_manager-2026-06-16-design-overhaul-gap-analysis-validated.md`
(TASK-413, `origin/main @ 77150e0`). All rulings per founder msgs 62 + 66
(THR-010), 2026-06-16. Scope locked. — product_lead, TASK-415.*
