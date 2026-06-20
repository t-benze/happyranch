# PRD — HappyRanch Design Overhaul (Direction A "Pasture")

> **STATUS: DRAFT — discussion input for `engineering_manager`, NOT a committed roadmap.**
> No roadmap commitment, no external timelines, no scope sign-off implied. All of
> that needs Founder approval. This document exists to give engineering a
> structured, user-anchored statement of *what Direction A is trying to be* so we
> can have an informed gap conversation. The authoritative gap call is **joint**
> (see companion `…-gap-analysis-draft.md`).
>
> **Origin:** THR-010 (founder directive, msg 44), TASK-411. Author: `product_lead`.
> Date: 2026-06-16.

---

## 0. Method, provenance, and how to read this doc

**What I actually read (facts are grounded in these):**
- The **Direction A design bundle** fetched live from Claude Design
  (`HappyRanch - Direction A.html` → `screens/a-*.html` (18 screens) + `surfaces/*.jsx`
  + `screens/shell.js` nav + all **10 chat transcripts** where the user's intent lives).
  The fetch worked in-session (gzip→tar bundle); contents are **not** guessed.
- The current-state ground truth: `engineering_manager-2026-06-16-design-handoff-package.zip`
  → `product-update.md` (surfaces A–L) and `scope-and-diff.md` (source-verified
  against `main` HEAD `77150e0`, with PR/commit citations).

**Confidence legend used throughout** (per our fact/inference discipline):
- **[FACT]** — directly present in the Direction A source or the EM current-state docs.
- **[ASSUMPTION]** — my inference, stated so you can challenge it.
- **[NEEDS-VALIDATION]** — a real product decision or user-data question that is
  unresolved; do not build past it without a ruling.

**Important scope note.** Direction A's medium is HTML/JSX prototypes with
*representative mock data*. Two parallel renderings exist for several surfaces
(the connected `a-*.html` "Pasture" screens and the `surfaces/*.jsx` components);
where they disagree, that disagreement is itself a product decision and is flagged.
There are also **chat-vs-final-prototype tensions** — places where the user stated
a decision in chat that the shipped prototype screen does not fully reflect. Those
are the highest-value open questions and are called out explicitly.

---

## 1. Product thesis / north star (the "why")

**[FACT, from chats]** HappyRanch is a **local-first runtime where a solo founder
boots and supervises a "company" of AI agents on their own machine** — "a
one-operator amplifier." The animating metaphor: *your agents are the livestock,
you're the rancher* (做牛做马).

The redesign is organized around three feeling-words the user returns to again and again:

1. **Calm / ambient awareness.** The home surface's job is *"is anything on fire?"*
   and *"this is what your org did and this is what needs you."* **"Calm is the
   empty queue."** The product should not manufacture urgency.
2. **Honesty (the load-bearing principle).** **[FACT]** The UI must render **only
   what the orchestration system genuinely knows or stores** — never synthesized
   interpretations, invented danger tiers, psychoanalysis of agents, fabricated
   clusters, or progress it can't measure. The user's own words: *"I'd been
   displaying script-level semantics the orchestration daemon can't actually
   derive."* This is the dominant **non-visual requirement** across the entire
   redesign and the single most important acceptance lens.
3. **Lower density, modern operator feel.** *"I prefer less density… more similar
   to a modern app like Claude, Codex, Slack."* The product is destined to be
   embedded as a **webview inside a native macOS/Windows shell** (hence the desktop
   window chrome in the prototype) and to be **built by engineers and released to
   end users** — not a personal throwaway.

**Why this matters for the build:** "honesty" and "calm" are not decoration. They
are *cut* criteria. Any feature that requires the UI to assert something the daemon
can't substantiate (a danger ranking, a citation count, an interpreted failure
pattern) is **out of scope until the data layer exists** — see §5 (the "no" list).

---

## 2. Cross-cutting principles & constraints (apply to every surface)

| # | Principle | Source | Acceptance implication |
|---|---|---|---|
| P1 | **Render only system-stored facts.** No synthesized interpretation. | [FACT] chats 2–3 | Every datum on screen must trace to a daemon field, audit event, or CLI output. Reviewer: reject any "computed insight" with no backing store. |
| P2 | **Calm by default.** Surface attention, never manufacture it. | [FACT] | Default/empty/quiet states must read as *intentional*, not broken or alarming. "Quiet dream" and "0 failed" are first-class positive states. |
| P3 | **One owner per capability.** Dashboard = "is anything on fire" home · Audit = forensic record (what happened) · Spend = burn (what it cost). | [FACT] chat8 consolidation ruling | No surface duplicates another's job. Spend owns cost; Dashboard only *glances* at it and links out. |
| P4 | **Left sidebar nav + desktop window chrome**, retiring the ~9 top-tab bar. | [FACT] chat10 | Nav is a grouped left rail; shell carries window controls for the native wrapper. |
| P5 | **Theme is toggleable and persists** across navigation (Direction A is light-first "Pasture"; dark supported). | [FACT] | Theme persists via storage; both themes are first-class. |
| P6 | **Consistent object/ID taxonomy with click-through everywhere.** `THR-`/`TASK-`/`JOB-`/`PR #`/agent/executor. | [FACT] | Every ID is a link; provenance (which thread/agent produced a thing) is first-class metadata. |
| P7 | **Cost transparency is a pillar**, surfaced honestly (tokens + cache + dollars reconciled, no cryptic model labels). | [FACT] | Token "churn" = `total` only; cache reads in a **separate** column, never folded into churn (matches KB `token-usage-surface-ownership-doctrine`). |

---

## 3. ⭐ FIRST-CLASS SECTION: Information Architecture & the "homeless surfaces"

This is the heart of Direction A and the recurring THR-010 question. **Direction A's
central thesis is an IA move: give every homeless surface a home, and collapse the
overgrown tab bar into a calm, grouped sidebar.**

### 3.1 Current IA (ground truth) vs Direction A IA

**[FACT] Current nav (9 flat top tabs):**
`Dashboard · Threads · Tasks · KB · Audit · Agents · Jobs · Artifacts · Assistant`.
Default landing route = **Threads**. Talks removed (PR #103). Feishu removed (PR #98).

**[FACT] Direction A nav (left sidebar, two groups + footer), from `shell.js`:**
- **Primary group:** `Home · Threads · Tasks · Agents · Knowledge · Artifacts`
- **"Operate" group:** `Spend · Dreams · Schedule · Audit`
- **Footer:** `Settings` (+ founder identity block, theme toggle, org switcher)
- **Assistant is NOT a tab** — it is an **omnipresent dock** opened from the
  "Ask or search" pill or **⌘K** from any screen.
- Default landing = **Home** (Dashboard).

### 3.2 The deltas, named

| Move | Current | Direction A | Category |
|---|---|---|---|
| **Spend gets a home** | Dashboard panel only; no page | Dedicated **Spend** page (sole cost owner) | **Missing → new surface** |
| **Dreams gets a home** | None; dream-threads indistinguishable in Threads inbox | Dedicated **Dreams** feed + KB-candidate review queue | **Missing → new surface** |
| **Schedule/work-hours gets a home** | CLI-only (`happyranch work-hours …`) | Dedicated **Schedule** surface | **Missing → new surface** |
| **Assistant becomes a dock** | Dedicated `/assistant` page with xterm terminal | Omnipresent conversational **dock** (⌘K) | **Needs-rework** |
| **Settings becomes a page** | Modal dialog from TopBar gear | Dedicated **Settings page** with sub-nav | **Needs-rework** |
| **Dashboard becomes the home** | Default landing is Threads | Default landing is **Home/Dashboard** | **Divergent** |
| **KB → "Knowledge"** | Tab labeled "KB" | Renamed "Knowledge"; folder rail | **Divergent (rename) + rework** |
| **Jobs tab retired** | Dedicated **Jobs** tab | **No Jobs list** in Direction A; jobs reached contextually via threads/tasks/artifacts (only Job *detail* exists) | **Divergent — NEEDS-VALIDATION** |
| **Nav grouping** | Flat 9 tabs | Two semantic groups (primary vs "Operate") + footer | **Divergent** |

### 3.3 The four homeless surfaces — resolution and the questions they leave open

**1) Token-usage / Spend.** [FACT] Direction A makes Spend a **full page** and the
*single owner* of token/cost burn (chat8). The dashboard's old "Top token threads"
panel collapses to a single glanceable "this week's burn" stat that **links into
Spend**. Audit keeps cost only as a per-event detail. **[NEEDS-VALIDATION]** The
prototype shows two irreconcilable cost framings: the connected HTML screen asserts
**`$0.00 · local executors · no metered API`** (thesis: executors are flat-rate
local CLIs, so *tokens are the budget*), while `surfaces/spend.jsx` shows real
dollars (`$18.40/week`, `~$0.54/1M`). **This is a genuine product decision, not a
mock-data slip** — see §4 Spend and §6 Q1.

**2) Dreams (nightly reflection).** [FACT] Promoted to a **first-class reflection
surface**: a feed of nightly reflections (rendered as human, narrative pull-quotes),
a **KB-candidate review queue** with Accept / Edit-first / Dismiss, and a schedule
glance. This directly closes current §F ("still the homeless surface — a
dream-thread is indistinguishable from a coordination thread") and supplies the
**founder accept/reject affordance** for proposed knowledge that current §F flags as
missing.

**3) Work-hours / Schedule.** [FACT] The backend-only capability (current §I) gets a
surface: an on-schedule rail / week grid, per-agent work-hours, named **recurring
"wakes"** as first-class objects, a **"While you were away"** wake feed, and a
**calm off-switch** ("present but calm — control lives in detail, not shouting").
Encodes a non-interrupting operating model: *finish in-flight work, then idle; hold
escalations until morning; urgent override is opt-in.*

**4) System Assistant.** [FACT] This was the prior package's **#1 open question**
(page vs dock vs palette). **Direction A's answer is a persistent conversational
DOCK** ("a conversational assistant that runs your runtime, not a terminal"),
opened via ⌘K / the "Ask or search" pill, Esc to close. Direction B used a command
palette. **[NEEDS-VALIDATION]** Direction A *also* keeps an Assistant configuration
home in Settings, and the user left "mix the directions" (Pasture visuals + palette
assistant) explicitly open. The current `/assistant` xterm page would be retired in
favor of the dock — confirm we're comfortable removing the raw terminal entirely vs.
keeping it behind an "Open full session" link (the dock header offers exactly this).

---

## 4. Per-surface PRD

Each surface: **Purpose · Target user + problem · Desired interaction logic ·
States · Edge cases · Acceptance criteria (measurable where possible).** Ordered by
the Direction A sidebar.

---

### 4.1 Home (Dashboard)

- **Purpose.** The calm landing/triage surface: "is anything on fire, and what wants
  me right now?" in one look. **[FACT]** Reframed from a *status board* to a
  *what-needs-you triage*.
- **Target user + problem.** The solo founder who has been away and needs ambient
  awareness without reading the audit log. Problem: today the dashboard isn't even
  the home screen (landing is Threads), and it's a crowded text wall.
- **Desired interaction logic.** [FACT]
  - A narrative greeting that states the situation in plain language
    ("**Two things need you, the rest is humming.**" / "9 tasks completed, 15
    questions waiting on you").
  - A **"Today" heartbeat** — a 24-bar hourly sparkline (quiet hours dimmed) +
    counters: Completed / Failed / Active now / KB entries / Spend today.
  - A **"This week's burn"** glance card that **links into Spend** (P3) — does not
    duplicate the Spend page.
  - An **Org pulse** per-team 7-day acceptance table.
  - Auto-resolution surfaced as a *calm positive* metric ("**6 escalations cleared
    by supersede this week**") — closes current §H ("invisible auto-resolution").
- **States.** Calm/empty ("nothing needs you"), normal, and a "lots waiting" state
  that demotes the long tail ("+12 more — mostly status reports you can skim later").
  Day-0/empty must feel intentional (P2).
- **Edge cases.** Zero agents / day-0 org; an all-quiet day (must not look broken);
  a flood of escalations (long-tail demotion).
- **⚠ Chat-vs-prototype tension [NEEDS-VALIDATION].** In chat the user said *"I
  prefer not to have the Waiting-on-you module in the dashboard, I rarely use it"*
  and moved the interactive escalation queue to **Audit**. But **both** shipped
  Direction A renderings (connected `a-dashboard.html` and `dashboard.jsx`) still
  carry a prominent "Waiting on you" triage list (tightened: kind→verb action
  mapping, 2-line clamp, demoted tail). **Decision needed:** does the home keep a
  *tightened* triage list, or just a glanceable "Waiting on you · N" that links to
  the queue's real owner? (See §6 Q2.)
- **Acceptance criteria.**
  - AC1: Default landing route resolves to Home, not Threads. **[verifiable]**
  - AC2: Every number on the home traces to a stored daemon fact (P1) — no
    interpreted/forecast values.
  - AC3: The burn figure on Home equals the Spend page's same-window figure (single
    source of truth; P3).
  - AC4: A founder returning after N hours can name the count of items needing them
    and open the first one in ≤2 clicks. **[usability target — NEEDS-VALIDATION via
    founder dogfood]**

---

### 4.2 Threads (list + detail)

- **Purpose.** Founder-visible, multi-agent **broadcast** conversations for
  coordination and cross-team handoff. The sole collaboration surface (Talks
  removed; see edge cases).
- **Target user + problem.** Founder needs one async place to coordinate across
  teams and stay human-in-the-loop without babysitting. Today: broadcast legibility
  is poor ("who will act on this?"), system events and messages share a flat
  transcript, and the turn budget is invisible.
- **Desired interaction logic.** [FACT]
  - **List:** segmented filter (`All / Waiting on you / Active / Done`) with counts;
    each row leads with the **last speaker** ("**engineering_manager:** JOB-083 is
    awaiting your approval…") and an **overlapping avatar stack** that encodes
    multi-agent participation at a glance; unread rows tinted; status pills
    (`waiting on you` / `active` / `review` / `merged` / `idle`) and a green `live`
    pill for an in-progress thread.
  - **Detail:** 2-col — scrolling transcript + a 300px rail (Participants / Linked
    tasks / Artifacts / This-thread stats: messages, token churn, est. cost, opened).
    Composer placeholder *"Reply to the thread, or @mention an agent…"*.
  - **Distinct treatment for system/execution content:** agent messages can embed
    **structured tool-run cards** (e.g. a `ran · happyranch tokens --thread THR-021`
    block with churn/cache/cost/model) and **task-reference cards** — visually
    separated from narrative prose. This closes current §B ("system events vs
    messages share a flat transcript").
- **States.** open / archived; `waiting on you` (warn, dotted); `live`; your-turn
  styling; X/500 turn counter surfaced (not just at the cap).
- **Edge cases.** **Talks is gone** — current product is Threads-only (PR #103).
  Direction A's *chat-era* transcripts still reference a 1:1 "Talks" coaching
  surface, but the **connected Direction A sidebar omits Talks**, so the prototype
  already aligns with Threads-only. **[ASSUMPTION]** Treat Threads-only as the
  baseline; the chat-era Talks concept is superseded — do not reintroduce it without
  a Founder reversal of PR #103.
- **Acceptance criteria.**
  - AC1: From the list, the founder can tell *who spoke last* and *whether it needs
    them* without opening the thread. **[verifiable]**
  - AC2: System/tool-run events are visually distinct from human/agent prose. **[verifiable]**
  - AC3: Turn budget (X/500) is visible before the cap is reached. **[verifiable]**
  - AC4: @mention semantics resolve to a real participant set — if @mention does not
    actually change routing in the daemon, the affordance must not imply it does (P1). **[NEEDS-VALIDATION]**

---

### 4.3 Tasks (list + detail)

- **Purpose.** The org-wide work board (as a **list**, not a kanban) + a per-task
  decision/lineage surface.
- **Target user + problem.** Founder needs to read delegation *structure* instantly
  and answer "why is this blocked/superseded?". Today: tree legibility is poor and
  "resolved_superseded" reads as just another grey chip.
- **Desired interaction logic.** [FACT]
  - **List:** dense 44px one-line rows; group-by `Status / Agent / Thread`; status
    groups including **Resolved (superseded)** (dimmed, retained). **Bidirectional
    lineage is visible inline**: `↳ supersedes TASK-381` (forward) and `→ TASK-407`
    (back-pointer on the dead task) — closes current §H ("no link from superseded
    task to its continuation").
  - **Detail:** a **connected vertical chain timeline** with node states
    (done / current-with-glowing-ring / blocked), where a blocked downstream node
    **names its blocker** ("waits on TASK-349"); a property rail (assignee, executor,
    thread, job, churn, created, priority); an append-style activity log with mono
    timestamps; contextual primary action (e.g. "Approve JOB-083").
- **States.** Pending / Active / In review / Blocked / Completed / Failed /
  Resolved (superseded). Current node highlighted; blocked node shows blocker.
- **Edge cases.** Deep/wide chains (the user **rejected** an infinite-scrolling
  client-grouped lineage forest — resolution is **root-only stream with
  severity-max rollup**, lineage expand-on-demand); revisit links vs subtasks vs
  chain legs must be visually distinguishable.
- **Acceptance criteria.**
  - AC1: List is a list, not a board (explicit user requirement). **[verifiable]**
  - AC2: From a superseded task the founder can reach its successor in 1 click, and
    vice-versa. **[verifiable]**
  - AC3: The brief renders as **raw monospace markdown with a "Show full" toggle** —
    no synthesized "N-slot" parsing (P1; the parser does not exist). **[verifiable]**
  - AC4: A blocked task always shows *what it is blocked on*. **[verifiable]**

---

### 4.4 Agents

- **Purpose.** Editable agent roster + rich detail (resolves the executor-switch and
  prompt-visibility questions, current §J/§L).
- **Target user + problem.** Founder configures and supervises agents. Today: repos
  and system prompts are visible (read-only drawer) but **not editable**; executor
  switching, repo assignment, and prompt edits still require CLI/file edits.
- **Desired interaction logic.** [FACT]
  - Two-pane (roster list + roomy editable detail — explicitly **"not a cramped
    drawer"**). Detail surfaces **accountability metrics on the agent itself**
    (e.g. "42 tasks done · 88% accept rate"), role, **editable** system prompt,
    **executor switch** (segmented `codex / claude / pi`), team, **repo chips**
    (add/remove), tool chips, and a **"Can act autonomously" per-agent toggle**
    ("skip founder approval for low-risk actions") that ties into the
    Schedule/Assistant approval model.
  - "Edits take effect on this agent's next task."
- **States.** per-agent active/idle live dot; unsaved-edits state with Save/Reset.
- **Edge cases.** Renaming an agent referenced by running tasks; switching executor
  mid-flight (applies next task, not in-flight); autonomy toggle interaction with
  founder-gated actions (merges/protocol edits must *still* gate regardless — P1/
  safety).
- **Acceptance criteria.**
  - AC1: Founder can change an agent's executor from the UI and it persists. **[verifiable]**
  - AC2: Accountability metrics shown are real stored counts, not estimates (P1). **[verifiable]**
  - AC3: Autonomy toggle never bypasses the founder gate on protocol edits / merges
    (safety invariant). **[NEEDS-VALIDATION with EM on what "low-risk" means]**
  - AC4: System prompt is editable inline and the change is what the next session
    receives. **[verifiable]**

---

### 4.5 Knowledge (KB)

- **Purpose.** The org knowledge library, browsable by folder, with a dream-candidate
  review gate.
- **Target user + problem.** Founder curates durable cross-agent knowledge and
  decides which dream-proposed candidates join the library. Today: KB has no usage
  signal on the page (view tracking is CLI-only, current §J) and no candidate-review
  affordance.
- **Desired interaction logic.** [FACT]
  - **List:** a folder rail (`All entries`, `Engineering > review/qa/build`,
    `Org > protocols / from dreams`) + a stacked entry feed. Dream-proposed entries
    are **visually distinct** (accent moon glyph) and quarantined as "pending review";
    live entries surface **trust signals: "used by N agents · updated 2d ago · v3"**
    (closes current §J — KB view tracking gets a web home).
  - **Detail:** a fully-rendered doc with a **sticky candidate banner** (Accept /
    Edit-first / Dismiss) for dream candidates — *review-in-context*, not a separate
    queue; provenance links back to the originating dream; properties + revision
    history.
- **States.** live vs `pending` (candidate); "used by — not yet live" for candidates.
- **Edge cases.** Accepting a candidate (it joins the library + gains a version);
  editing before accept; type-differentiated rendering (precedent / ruling / sop /
  reference).
- **⚠ Scope cut [FACT].** **Citation UI (cited-by counts, "load-bearing"/"uncited"
  badges) is explicitly DROPPED for v1** — no backing audit event exists yet (P1).
  Deferred until `kb_consulted` / `kb_referenced` audit events ship.
- **Acceptance criteria.**
  - AC1: A dream candidate can be Accepted / Edited / Dismissed from the entry view,
    and Accept makes it live. **[verifiable]**
  - AC2: Usage ("used by N agents") shown only where the daemon actually stores it
    (it does — `kb stats`); never fabricated (P1). **[verifiable]**
  - AC3: No citation/"load-bearing" badge ships in v1. **[verifiable]**

---

### 4.6 Artifacts

- **Purpose.** The gallery of everything agents produced (PRs, docs, patches,
  designs), tied back to the thread/agent that produced it.
- **Target user + problem.** Agents hand files to each other and the founder. Today:
  flat list, no preview, no grouping; folder/nested-key names render as flat strings
  (current §D); Artifacts-vs-KB boundary is unclear.
- **Desired interaction logic.** [FACT]
  - **List:** a **3-col card grid** with type filter (`All / Pull requests / Docs /
    Patches / Designs`), each tile carrying a kind pill, status tag
    (`merged / final / applied / open / draft / v2`), and **provenance** (producing
    thread + agent + age) as first-class metadata. (PR tile can literally read
    "blocked on JOB-083".)
  - **Detail (for a PR):** checks list that renders **maker-checker review AND the
    founder gate as CI-style checks** alongside automated suites (a blocking
    "needs you" check), files-changed with +/− counts, a diff snippet, property rail.
- **States.** per-artifact status; PR checks: passed / queued / blocked-needs-you.
- **Edge cases.** **[NEEDS-VALIDATION]** Direction A's Artifacts list is a **flat
  recency grid — it does NOT visualize folders**, even though the backend now
  supports nested keys (current §D). So Direction A does *not* answer current §D's
  "how does the web show folder structure?" question. Decision: ship flat grid for
  v1 and defer folder browsing, or design folder browsing now? (See §6 Q4.)
- **Acceptance criteria.**
  - AC1: Every artifact shows which thread + agent produced it. **[verifiable]**
  - AC2: PR detail shows real check states from stored CI/review/job status (P1). **[verifiable]**
  - AC3: Artifacts-vs-KB distinction is legible to a new founder (copy/affordance). **[NEEDS-VALIDATION via dogfood]**

---

### 4.7 Spend (homeless → home) ⭐

- **Purpose.** The **single owner** of token/cost observability; reconciles tokens ↔
  cache ↔ dollars and resolves cryptic model labels.
- **Target user + problem.** "What am I spending, and on what?" — a first-order
  founder question with no honest visual home today (panel only).
- **Desired interaction logic.** [FACT]
  - A hero "this week's burn" card (window toggle **24h / 7d / 30d**, 7d default)
    with a fresh-vs-cache split chart; a **"where it went" breakdown** by
    **team & agent**, by **thread**, and by **model** (segmented `Thread / Agent /
    Model`); a **"Top threads by churn"** table; an Export affordance.
    Cache savings is framed as the **hero virtue** ("Cache saved 241M tokens · 57%
    of reads served from cache").
- **States.** window-scoped; trend delta vs prior period ("▼12%").
- **Edge cases / invariants.**
  - **Churn invariant [FACT, KB-binding].** `total = input + output + reasoning`.
    Cache reads render in a **separate column** and are **never** folded into churn
    or used as a ranking key (KB `token-usage-surface-ownership-doctrine`). The Spend
    page must obey this.
  - **Model-label honesty.** Cryptic labels (`(unknown — pre-fix)`,
    `(unknown — ANOMALY)`, `(mixed)`, `(cli-unreported)`) must render as *labeled*
    values, never blanks, and never be silently "cleaned up" into a wrong model
    (O1–O4 of the doctrine).
- **⚠ Top product decision [NEEDS-VALIDATION] — see §6 Q1.** Dollars vs `$0.00`.
  The connected screen's thesis is **"tokens are the budget; $0.00 because executors
  are flat-rate local CLIs with no metered API"**; the JSX variant shows real
  dollars. We must pick the honest model: (a) tokens-only with $0 cost, (b) real
  dollar estimates with a stated per-1M rate, or (c) both, clearly labeled
  estimate-vs-actual. This determines what "Spend" even means.
- **Acceptance criteria.**
  - AC1: Cache reads never appear inside the churn/total number. **[verifiable, binding]**
  - AC2: Every model label is non-blank and never a fabricated correction. **[verifiable]**
  - AC3: The dollar model is decided and applied consistently across Home, Spend,
    Threads, and Audit (no two surfaces disagree on cost). **[verifiable once Q1 ruled]**

---

### 4.8 Dreams (homeless → home) ⭐

- **Purpose.** First-class **nightly-reflection** surface: agents reflect off the
  task clock, write learnings, propose KB candidates, and open founder threads only
  when output is worth attention.
- **Target user + problem.** "Agents should get better over time." Today there is
  **no dream surface** — a dream-thread is indistinguishable from coordination
  (current §F), and there's no founder accept/reject affordance for candidates.
- **Desired interaction logic.** [FACT]
  - A chronological **reflection feed** (reflections rendered as italic, human
    pull-quotes — *narrative, not telemetry*), a **KB-candidate review queue**
    (Accept / Edit-first / Dismiss, with confidence badges), and a **schedule
    glance** ("Next run tonight · 03:00 · 5 agents · America/LA").
  - **Dream detail:** quote → stat strip (diffs reviewed / recurring findings /
    tokens·duration) → narrative doc → proposed-knowledge cards → "Open reflection
    thread"; a Replay affordance (JSX variant).
- **States.** Active dream (has candidates, badge "3 to review") vs **quiet dream**
  ("Quiet dream — nothing escalated · private learning saved") — quiet is an
  explicit *non-alarming* valid state (P2).
- **Edge cases.** A dream with zero output; a dream that opens a founder thread
  (must be marked as dream-originated, closing §F); KB candidate → Knowledge accept
  flow shares the same gate as §4.5.
- **Acceptance criteria.**
  - AC1: A dream is visually distinguishable from a coordination thread everywhere it
    appears. **[verifiable]**
  - AC2: Founder can Accept/Edit/Dismiss a KB candidate from Dreams or Knowledge and
    the result is consistent. **[verifiable]**
  - AC3: Reflection text shown is the agent's actual stored reflection, not a summary
    the UI invents (P1). **[verifiable]**

---

### 4.9 Schedule (homeless → home) ⭐

- **Purpose.** Give agents a working-day rhythm and make unattended scheduling
  visible and trustworthy.
- **Target user + problem.** "Recurring work shouldn't need a human to kick it off,
  but unattended scheduling needs visibility and trust" (current §I). Today: CLI-only.
- **Desired interaction logic.** [FACT]
  - An overview (week grid with working-hour + dream bands, or a per-agent 24h
    timeline with a "now" line and scheduled-wake dots) + **per-agent work hours** +
    **named recurring "wakes"** as first-class objects (e.g. "Morning standup digest
    · Weekdays 09:00 · in 13h"; on/off toggles) + a **"While you were away"** wake
    feed.
  - **Behavior toggles** encode the calm operating model: *Finish in-flight work* /
    *Hold escalations* (until morning) / *Urgent override* (opt-in: "allow @mention to
    wake an agent").
- **States.** working-hours vs dream-window vs paused (weekends); per-job active vs
  paused; on-demand agent ("on demand — no fixed hours").
- **Edge cases.** Timezone display; a wake that fires while the founder is away
  (lands in "While you were away"); an agent with no fixed hours.
- **Acceptance criteria.**
  - AC1: Founder can see and toggle every recurring wake and per-agent work-hours
    window from the UI (parity with the `work-hours` CLI). **[verifiable — depends on
    backend mirror, see gap analysis]**
  - AC2: Schedule reflects the daemon's actual scheduler state, not a UI mock (P1). **[verifiable]**

---

### 4.10 Assistant (dock, not a page) ⭐

- **Purpose.** A **conversational operator** that both *answers* and *acts on* the
  runtime — "an assistant that runs your runtime, not a terminal."
- **Target user + problem.** Founder wants to query and drive the system
  conversationally without memorizing CLI verbs. Today: a config-first `/assistant`
  page with a raw xterm console (current §G) — "is a raw terminal the right
  affordance?".
- **Desired interaction logic.** [FACT]
  - An **omnipresent dock** opened from the "Ask or search" pill or **⌘K** on any
    screen (Esc closes); header shows connection status + executor chip + an
    "Open full session / Open terminal" escape hatch + minimize.
  - The assistant **answers and acts**: it surfaces the **command it ran** inline
    (`ran · happyranch tokens --thread THR-021` with churn/cache/cost/model — a
    transparency affordance) and offers **one-click action chips** ("Approve
    JOB-083", "Open THR-021", "Show the diff"). Calm reassurance copy ("Nothing is
    on fire").
- **States.** Connected / executor-configured; surfaced errors must be honest
  (current §G shows verbatim `assistant_executable_not_found` — keep honest but
  legible).
- **Edge cases.** Uninitialized / stale_or_broken assistant (Init/Repair lives in
  Settings §K integration); an action chip whose underlying op needs founder gating
  (must route through the same approval, not silently execute — P1/safety).
- **[NEEDS-VALIDATION] — see §6 Q3.** Confirm: dock replaces the dedicated page;
  keep the terminal only behind "Open full session"? And is the assistant surface
  user-configurable (Settings exposes "Assistant surface: Dock / Palette / Full
  page" — the A/B question surfaced as a setting)?
- **Acceptance criteria.**
  - AC1: ⌘K opens the dock from every surface; Esc closes; state persists. **[verifiable]**
  - AC2: Any command the assistant runs is shown verbatim (transparency). **[verifiable]**
  - AC3: An assistant action that requires founder approval routes through the
    standard gate, never auto-executing a protocol edit / merge (safety). **[verifiable]**

---

### 4.11 Settings (page, not dialog)

- **Purpose.** The in-app configuration surface (resolves "dialog vs page", current §K).
- **Target user + problem.** Founder configures org + agents + assistant without
  editing YAML and restarting. Today: a single modal dialog with three stacked
  sections; no `/settings` route to bookmark/deep-link.
- **Desired interaction logic.** [FACT]
  - A dedicated **page** with a sticky left sub-nav (e.g. `Assistant · System ·
    Organization · Agents · Executors · Billing`) + a field panel + a sticky save bar.
  - **Org section editable** (Nightly dreaming: enabled / schedule / timezone / agent
    mode / catch-up; Threads: default turn cap / session timeout) with **explicit
    live-vs-restart labeling per field** ("Applies immediately" vs "restart to
    apply") — a trust/transparency affordance.
  - **[FACT]** Agent-name fields use **chips with autocomplete**, replacing the
    error-prone comma-separated text fields current §K flags.
  - System Assistant config (status, Init/Repair, "Open terminal") lives here,
    integrating §4.10.
- **States.** dirty/Save-Discard; per-field restart badges; assistant status badge
  (Uninitialized / Configured / Stale-or-broken).
- **Edge cases.** Save that requires a restart (must tell the founder *how*); typo'd
  agent name (autocomplete prevents); read-only System rows (no false affordance).
- **⚠ Open IA question [FACT, current §K/§L].** Settings (org/assistant config) and
  the Agents surface (per-agent config) both touch configuration and are visually
  disconnected. Direction A leaves whether to **unify Agents + Settings into one
  admin surface** open. (See §6 Q5.)
- **Acceptance criteria.**
  - AC1: `/settings` is a real bookmarkable route. **[verifiable]**
  - AC2: Every field is correctly labeled live-apply vs restart-required, matching
    actual daemon behavior (P1). **[verifiable]**
  - AC3: Agent-name inputs autocomplete from the real roster (no free-text typos). **[verifiable]**

---

### 4.12 Audit

- **Purpose.** The immutable, append-only forensic record — "what happened, who, and
  when" — exportable for compliance.
- **Target user + problem.** Founder (or a future auditor) needs a trustworthy,
  filterable record. Owner of the *forensic* job (P3 consolidation).
- **Desired interaction logic.** [FACT]
  - A day-grouped timeline; color-coded event classes (completed / merge /
    escalation / failure); every entry carries **executor + token cost**; an
    **Event-types legend with counts** (Dispatch 38 / Completed 52 / Merge 9 /
    Escalation 15 / Failure 0) that doubles as a filter; a promoted **mono query
    language** (`actor:dev_agent`, `action:merge since:7d`) reinforcing the
    immutability framing; Export.
- **States.** event classes by color; "Failure 0" is a first-class calm state (P2).
- **⚠ Chat-vs-prototype tension [NEEDS-VALIDATION] — see §6 Q2.** In chat the user
  decided Audit should **own escalations end-to-end** (an Open/Resolved toggle; the
  working reply → promote-to-KB → resolve loop). The **connected `a-audit.html`
  screen does not show this** — it renders only the append-only log + query examples.
  So the "where does the interactive escalation queue live — Dashboard or Audit?"
  question is **not resolved by the prototype**. This is tightly coupled to the
  Home "Waiting on you" tension (Q2).
- **Acceptance criteria.**
  - AC1: Every audit line is immutable, attributable (actor), and exportable. **[verifiable]**
  - AC2: Filtering by event type / actor / time works and maps to stored events (P1). **[verifiable]**
  - AC3: The escalation-queue ownership (Home vs Audit) is decided before build. **[NEEDS-VALIDATION — Q2]**

---

### 4.13 Jobs — ⚠ no list surface in Direction A [NEEDS-VALIDATION] — see §6 Q6

- **[FACT]** Direction A ships **only a Job *detail*** screen (a founder approval
  surface) and **no Jobs list / index** — and **no Jobs tab** in the sidebar. Jobs
  are reached **contextually** via threads, tasks, and artifacts (the job detail's
  breadcrumb is "Back to THR-021" — jobs are thread-anchored).
- **Current product has a dedicated Jobs tab.** So Direction A implies **retiring the
  Jobs tab** and making jobs purely contextual. This is a real IA decision, not an
  oversight, but it is under-specified (no list = no way to see "all jobs awaiting
  me" in one place).
- **Job detail desired logic [FACT].** Shows the **verbatim command/diff** + an
  **"If approved" causal cascade** ("protocol updated → PR #101 unblocks → TASK-351
  becomes runnable") + an attention approve-card ("This is a protocol change — it
  needs your sign-off") + "Routed via" (requester vs escalation path) + an "Ask the
  assistant" decision-support escape hatch. Honest attention signal = "🔑 needs
  credential" / "flagged for review" with a **uniform two-step confirm** (the system
  can't rank danger — P1; danger tiers were explicitly rejected).
- **Acceptance criteria.**
  - AC1: Job approval shows the real command/diff and a real downstream-impact list
    (no invented effects — P1). **[verifiable]**
  - AC2: No danger-tier ranking; uniform confirm. **[verifiable]**
  - AC3: Decision on whether a Jobs index exists is made before build (Q6). **[NEEDS-VALIDATION]**

---

## 5. The "no" list (defend the cuts as clearly as the build)

Direction A is as defined by what it **refuses** as by what it builds. These were
explicitly explored and **rejected** in the design sessions — re-proposing them
needs a Founder reversal:

| Cut | Why (honesty / scope) |
|---|---|
| Jobs **danger tiers**, target chips, progress bars, "Effect" line, dry-run preview | Not derivable from stored state (P1). |
| Agent **failure-pattern psychoanalysis** + prefab recommendations + invented clusters | Synthesized interpretation (P1). Replaced with counted facts + verbatim notes + real `failure_kind`. |
| KB **citation badges** / "load-bearing"/"uncited" sort | No backing audit event yet (P1). Deferred until `kb_consulted`/`kb_referenced` ship. |
| Tasks **board/kanban** view | User explicitly chose list. |
| Tasks **8-slot brief parser** | No parser exists; ship raw markdown + "Show full". |
| Dashboard heavy **"Waiting on you" reply module** (as originally built) | "I rarely use it" — at minimum tighten; ownership of the queue is Q2. |
| Direction B **"Mission Control"** (slate, ⌘K-palette assistant, Linear feel) | Not the chosen direction (Pasture/A won) — though "mix the two" is left open. |
| Reintroducing **Talks** | Removed in current product (PR #103); chat-era Talks is superseded. |

---

## 6. Open product decisions (must be ruled before/within the build)

These are the questions that block convergence. Several are **chat-vs-prototype
tensions** (the user decided X in chat but the shipped screen shows Y) — those are
the most important to close. The companion gap analysis repeats these as
gap-closure questions for `engineering_manager`.

- **Q1 — Spend's dollar model.** Tokens-only ($0, "tokens are the budget, local
  flat-rate executors") vs real dollar estimates vs both-clearly-labeled? Determines
  what every cost figure on every surface means. **[NEEDS-VALIDATION — Founder call;
  factual input from EM on whether executors are metered.]**
- **Q2 — Where does the interactive escalation queue live?** Home keeps a tightened
  triage list (as both prototype renderings show), or it moves to Audit
  (as chat decided)? They can't both own it (P3). **[NEEDS-VALIDATION]**
- **Q3 — Assistant: dock-only, or dock + retained terminal page + user-selectable
  surface?** Confirm the `/assistant` xterm page is retired behind "Open full
  session". **[NEEDS-VALIDATION]**
- **Q4 — Artifacts folders.** Ship flat recency grid for v1 (Direction A's answer)
  and defer folder browsing, or design folder browsing now that the backend supports
  nested keys? **[NEEDS-VALIDATION]**
- **Q5 — Unify Agents + Settings** into one admin surface, or keep separate? **[NEEDS-VALIDATION]**
- **Q6 — Jobs index.** Accept Direction A's "no Jobs list, contextual only" (retire
  the Jobs tab), or keep a lightweight "jobs awaiting you" index? **[NEEDS-VALIDATION]**
- **Q7 — Statefulness.** Do v1 interactions become real (approve updates state, agent
  editor saves) or remain click-through fidelity for the first cut? **[NEEDS-VALIDATION]**

---

## 7. Measurable success criteria (org-level, draft)

Tied to customer (founder) value, not output:
- **Calm:** founder can answer "is anything on fire?" in one glance on Home without
  opening Audit. (Dogfood test.)
- **Homes for the homeless:** Spend, Dreams, Schedule each reachable in 1 click from
  the sidebar; the prior "indistinguishable dream-thread" and "CLI-only schedule"
  gaps are closed.
- **Honesty:** zero UI elements assert facts the daemon can't substantiate (review
  gate enforces P1).
- **Cost legibility:** the founder can answer "what did this week cost, and on what?"
  on one surface (Spend), with a single consistent cost model across surfaces.
- **Time-to-decision:** a founder-gated approval (job/PR/protocol) is reachable and
  actionable in ≤2 clicks from Home. (Dogfood test.)

---

*End of PRD draft. Companion: `product_lead-2026-06-16-design-overhaul-gap-analysis-draft.md`.*
