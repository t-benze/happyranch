# PRD (FINAL) — HappyRanch Design Overhaul (Direction A "Pasture")
## Authoritative product spec **with per-surface interaction specifications**

> **🟢 RECONCILED 2026-06-17 against the FINALISED Direction A design — supersedes
> the 2026-06-16-bundle reconciliation.** The TASK-457 stale-design caveat is
> **RESOLVED**: the founder supplied a working handoff link, the **fresh fetch
> SUCCEEDED** (HTTP 200 gzip→tar bundle, §C.1), and this PRD is now reconciled
> against the real finalised design — not the older captured bundle. **All three
> §A deltas (A.1 autonomy toggle, A.2 @mention routing, A.3 KB "used by N agents ·
> v3") are RESOLVED** — the finalised design removed/reworded each to the locked
> decision. **No new locked-decision deltas** were found. New canonical source:
> `product_lead-2026-06-17-design-overhaul-direction-a-FINAL-fetched-bundle.tar.gz`.
> (Reconciliation by `product_lead`, TASK-460.)

> **🟢 §B FOLDED IN + LANDED 2026-06-17 (engineering_manager, TASK-461).** The
> `[needs EM feasibility validation]` register (§B) is **resolved and folded into
> every surface spec** — all inline validation tags removed. Verdicts: **B.1 / B.4
> / B.5 = DAEMON-BACKED** (render-only/derive against existing data + routes; the
> gated-chip guardrail holds — no new privileged path). **B.2 (KB "Edit first"
> edit-then-accept route) and B.3 (thread read-state) = DEFERRED for v1** per
> founder ruling (THR-010 msg 149 — **no new stores for v1 beyond A4**). v1 ships
> B.2 as **Accept / Dismiss + accept-then-edit-the-live-entry via existing KB edit
> paths** (no new edit-then-accept route); B.3 **omits persistent unread styling**
> (no new read-state store). Both moved to §6 (D10/D11). **§A = zero open deltas;
> roots-only Tasks + NO in-list toggle present (§4.3).** This is the **canonical
> build spec** on `design-overhaul`.

> **STATUS: AUTHORITATIVE FINALISED PRODUCT SPEC.** This finalises and supersedes
> `product_lead-2026-06-16-design-overhaul-PRD-build-spec.md` (TASK-415). It keeps
> that build-spec's locked scope verbatim and **adds detailed per-surface
> INTERACTION SPECIFICATIONS** (interaction logic, states + transitions,
> keyboard/gesture shortcuts, edge/empty/error states, measurable acceptance
> criteria), reconciled against the **finalised** Direction A design (fresh fetch
> SUCCEEDED 2026-06-17 — §C.1).
>
> **NOT a roadmap or timeline commitment.** Sequencing notes are dependency
> guidance, not dated commitments; any external timeline needs separate Founder
> sign-off. No `protocol/` changes are implied.
>
> **Ownership & next step.** product_lead owns this document. **engineering_manager
> ran the engineering feasibility / honesty / locked-decision validation pass and
> owns the `design-overhaul` branch** — EM landed it (TASK-461). The §B feasibility
> register is **resolved and folded in**; no `[needs EM feasibility validation]`
> tags remain.
>
> Author: `product_lead`, TASK-457, 2026-06-17 (THR-010, founder directive msg 111).
> **Reconciled against the finalised design: `product_lead`, TASK-460, 2026-06-17
> (THR-010, founder msg 140).**

---

## 0. How to read this finalised PRD

**The honesty principle (P1) is the dominant lens — for interactions too.** Every
interaction spec below asserts **only daemon-backed behavior**. A transition,
state, or affordance may render **only** what the orchestration system genuinely
knows, stores, or can do. Where an interaction's feasibility against the daemon is
uncertain, it was specified as **product intent** and EM-validated in §B (now
**resolved and folded in**) — never silently shipped as if backed.

**Ground truth for "what's backed today"** is
`engineering_manager-2026-06-16-design-overhaul-gap-analysis-validated.md`
(TASK-413, validated against `origin/main @ 77150e0`). This PRD has **no repo
access**; current-state claims trace to that document.

**What changed vs the build-spec (TASK-415).** This document is **additive** — the
build-spec's thesis, principles, IA, per-surface *scope*, no-list, deferred
register, and success criteria are **unchanged and still binding**. The net-new
material is:
1. **§2.5 Global interaction model** (shell, ⌘K dock, theme, nav, the shared state
   vocabulary the prototype does **not** yet define).
2. **An `INTERACTION SPEC` block inside every §4 surface.**
3. **§A Design-vs-locked-decision deltas** (founder decision required).
4. **§B EM feasibility validation register** (now resolved + folded into the surface specs).
5. **§C Verification appendix** (what playwright confirmed; fetch status).

**Reconciliation source (READ — affects trust in this doc).** The founder supplied
a **working** finalised-design handoff link (msg 140). The **fresh fetch SUCCEEDED**
this round — HTTP 200 gzip→tar bundle, decompressed and studied in full (§C.1). The
canonical source is now the **finalised design fetched 2026-06-17**, captured to
shared artifacts as
`product_lead-2026-06-17-design-overhaul-direction-a-FINAL-fetched-bundle.tar.gz`.
**This supersedes the 2026-06-16 captured bundle.** The prior stale-design caveat
(TASK-457) is **RESOLVED** — this PRD has now seen the real finalised design. A
file-level diff of all `a-*.html` surfaces, `shell.js`, and `ds.css` (old bundle vs
finalised) drove the §A re-check below.

**Class legend** (carried from the validated gap analysis): **RENDER-ONLY** (data
stored + exposed; frontend-only), **DERIVE** (data exists, needs a new
read/aggregation route; no schema), **DEFERRED 🚩** (new store / daemon behavior /
permission change → founder-gated, §6). The single schema change promoted into v1
is **A4** (`composed_from_dream_id`).

---

## 1. Product thesis / north star (unchanged — the "why")

HappyRanch is a **local-first runtime where a solo founder boots and supervises a
"company" of AI agents on their own machine** — a one-operator amplifier. The
redesign is organized around three feeling-words:

1. **Calm / ambient awareness.** Home answers *"is anything on fire?"* and *"what
   needs me?"* **"Calm is the empty queue."** Never manufacture urgency.
2. **Honesty (load-bearing).** Render **only what the orchestration system
   genuinely knows or stores** — no synthesized interpretation, invented danger
   tiers, fabricated clusters, or unmeasurable progress. **P1 — the single most
   important acceptance lens, for data AND interaction.**
3. **Lower density, modern operator feel** (Claude/Codex/Slack-like), destined to
   be embedded as a webview in a native macOS/Windows shell.

"Honesty" and "calm" are **cut criteria**, not decoration.

---

## 2. Cross-cutting principles & constraints (apply to every surface)

| # | Principle | Acceptance implication |
|---|---|---|
| P1 | **Render only system-stored facts.** No synthesized interpretation. | Every datum AND every interaction outcome traces to a daemon field, audit event, or CLI action. **Dominant lens.** Reject any "computed insight" or affordance with no backing. |
| P2 | **Calm by default.** Surface attention, never manufacture it. | Default/empty/quiet states read as *intentional*. "Quiet dream", "Failure 0", "nothing needs you" are first-class positive states (see §2.5.5). |
| P3 | **One owner per capability.** Home = "is anything on fire / what needs me now". Audit = forensic record + resolved history. Spend = token burn. | No surface duplicates another's job. |
| P4 | **Left sidebar nav + desktop window chrome**, retiring the ~9 top-tab bar. | Grouped left rail; shell carries window controls for the native wrapper. |
| P5 | **Theme toggleable and persists** across navigation (light-first "Pasture"; dark supported). | **Verified in prototype** (§C.2): `localStorage['hr-theme']`, restored on every load. |
| P6 | **Consistent object/ID taxonomy with click-through.** `THR-`/`TASK-`/`JOB-`/`PR #`/agent/executor. | Every ID is a link; provenance is first-class metadata, limited to **stored** fields. |
| P7 | **Cost transparency, surfaced honestly.** | Token "churn" = `total = input + output + reasoning` only; **cache reads in a separate column, never folded into churn or used as a ranking key**. **Dollars = `$0.00` / "not metered" in v1** (Q1). |

### 2.5 Global interaction model (NEW — applies to every surface)

The prototype's interactivity is **navigation-only**: the only *real* behaviors are
(a) the theme toggle, (b) the ⌘K assistant dock, (c) `location.href` navigation
between screens, and (d) a procedural schedule-grid render. **Every other control
(Accept/Save/Approve/filters/folders/agent-select/window toggles) is a presentational
stub with no handler.** The specs below therefore define real behavior the build
must implement; where the prototype already implements it, it is marked
**[prototype-verified]**.

#### 2.5.1 Shell & navigation
- **Left rail, two groups + footer** (P4). Primary: `Home · Threads · Tasks ·
  Agents · Knowledge · Artifacts`. "Operate": `Spend · Dreams · Schedule · Audit`.
  Footer: `Settings` + founder identity block + theme toggle + org switcher.
- **Active item** driven by the surface's `data-active`; nav counts (Threads/Tasks/
  Agents) and the Dreams attention dot are **real stored counts** (P1), not static.
- **"Soon"/disabled nav items** render `aria-disabled="true"` and suppress
  navigation; a `soon` tag fades in on hover. (Mechanism exists in shell;
  currently nothing is "soon".)
- **No "Jobs" nav item** [prototype-verified] — Jobs retired at the tab level (Q6).

#### 2.5.2 ⌘K Assistant dock (global) — [prototype-verified core]
- **Open:** `⌘K` / `Ctrl-K` (toggle), click the "Ask or search" pill, or any
  `[data-assistant-open]`. On open the dock gets `.open` and **focus moves to the
  composer input** (verified: active element becomes the composer after open).
- **Close:** `⌘K` again (toggle), **`Esc`** (verified: dock `.open`→false), click
  the scrim, or click the `✕`. Closed is the default; closed dock is
  `pointer-events:none` so it never blocks the page.
- **REQUIRED additions the prototype lacks (build must add):**
  - **Focus trap** while open (Tab cycles within the dock) and **focus restore**
    to the trigger element on close. *(Prototype moves focus in but does **not**
    trap or restore — accessibility gap.)* *(EM-validated B.6: **frontend-only** —
    no daemon dependency, touches no route.)*
  - **State persists across navigation** (open/closed + any composer draft). In a
    SPA this is trivial client state; specify it explicitly so a multi-route build
    doesn't drop it. AC: `⌘K` opens from every surface and the dock is the same
    instance.
- **`ran: <cmd>` transparency cards are REAL** (the dock executes real commands;
  A3).
- **Hybrid dock (OPTION 3, founder-ruled).** The dock uses a JSON-chat handshake
  over the EXISTING `/api/v1/assistant/session` WebSocket — bearer-subprotocol auth
  and the PTY attach contract are **frozen and byte-identical**. The server output
  pump starts in raw-text mode immediately (legacy xterm path unchanged); the
  structured frontend tolerates/ignores any raw PTY frame received before its
  handshake ack, so no user ever sees a raw frame. **Zero server-side change** to
  connect/upgrade/endpoint/auth/handshake/PTY-attach.
- **Action chips are reference-existing-ID deep-links only** — each chip
  (`"Open THR-021"`, `"Show diff PR #121"`, `"Approve JOB-083"`) navigates to
  the object's existing approval or detail surface. No propose-new-action chip;
  no self-approve/self-execute path (TASK-414 guardrail). See §4.10.

#### 2.5.3 Theme — [prototype-verified]
- Toggle in the top bar swaps light⇄dark, writes `localStorage['hr-theme']`,
  swaps the sun/moon icon, and **persists across navigation** (verified: set dark
  on Home → navigate to Tasks → still dark). Restored on every page load before
  first paint (no flash). Cross-frame `postMessage({type:'setTheme'})` supported
  for the native-wrapper preview.

#### 2.5.4 ID click-through (P6) — [prototype-verified pattern]
- `TASK-*` → task detail; `JOB-*` → job detail; `THR-*` → thread detail; `PR #*` →
  artifact detail; agent/executor chips → their surfaces. IDs are real links with
  `cursor:pointer`. **Note:** the prototype routes *every* row/card of a kind to a
  single stub detail page; the build must route by real ID.

#### 2.5.5 Shared STATE VOCABULARY (NEW — the prototype defines none of these)
The design system ships **no loading / empty / error / skeleton classes**; all
prototype data is static and fully populated. Every surface MUST implement these
four states honestly:

| State | When | Spec | Framing |
|---|---|---|---|
| **Loading** | Data in flight | Skeleton rows/cards matching final layout; no spinner-only blank. Never invent counts while loading. | Neutral |
| **Empty** | Query legitimately returns nothing | Plain one-line statement of the zero ("No artifacts yet", "No agents need you"). **No** fake placeholder rows. | **Calm/positive (P2)** — e.g. Home empty = "Nothing needs you right now." |
| **Quiet** | Intentional nothing, system worked correctly | First-class positive: **"Quiet dream — nothing escalated · private learning saved"** [prototype-verified copy], **"Failure 0"**. Visually distinct from Empty (this is a *good outcome*, not absence of data). | **Positive (P2)** |
| **Error** | A real backend/route failure | Honest, specific, with a **retry** affordance: "Couldn't load spend — retry". Never silently show stale/zero as if live (P1). Distinguish "0" (a real value) from "couldn't load". | Neutral, non-alarming |

> **P1 corollary for states:** a zero value that is **real** (Failure 0, $0.00 not
> metered, "viewed 0×") is **Quiet/Empty**, never **Error**. An unknown value
> because a route failed is **Error**, never rendered as 0.

#### 2.5.6 Keyboard & gesture baseline (NEW — applies app-wide)
- Global: `⌘K` (dock toggle), `Esc` (close any open overlay/dock/dialog).
- **List surfaces (Tasks, Threads, Knowledge, Artifacts, Audit):** specify
  `↑/↓` to move selection, `Enter` to open the selected row, `Esc` to clear
  selection. *(Prototype has **no** list keyboard nav — this is net-new frontend;
  EM-validated B.6: **frontend-only**, touches no route.)*
- All interactive controls reachable by `Tab` with a visible focus ring (the DS
  has focus-ring tokens; wiring is required).

---

## 3. Information Architecture (unchanged from build-spec — summarised)

- **Primary group:** `Home · Threads · Tasks · Agents · Knowledge · Artifacts`
- **"Operate" group:** `Spend · Dreams · Schedule · Audit`
- **Footer:** `Settings` (+ founder block, theme toggle, org switcher)
- **Assistant is NOT a tab** — omnipresent ⌘K dock (§4.10).
- **Jobs is NOT a tab** — retired; reached contextually + Home rollup + Audit
  history (Q6; §4.13). **[prototype nav-verified]**
- **Default landing = Home** (was Threads).

IA deltas IA-1…IA-10 and their v1 disposition are unchanged from the build-spec
§3.2 (all RENDER-ONLY, all v1). The Agents+Settings unification remains a
**non-blocking, post-v1** IA question (both ship separately).

---

## 4. Per-surface spec (scope unchanged; INTERACTION SPEC added)

Each surface repeats its locked **Purpose / v1 scope / Deferred / data-acceptance**
from the build-spec (unchanged, binding) and adds a new **INTERACTION SPEC**
(interaction logic · states + transitions · keyboard/gesture · edge/empty/error ·
interaction acceptance). Conflicts surfaced during reconciliation are cross-linked
to **§A**.

---

### 4.1 Home (Dashboard) — RENDER-ONLY + 1 DERIVE

- **Purpose.** The calm landing/triage surface: "is anything on fire, and what
  wants me right now?" in one look.
- **v1 scope** (unchanged): narrative greeting; **"Today" heartbeat** (24-bar
  hourly sparkline, quiet hours dimmed) + counters (Completed / Failed / Active now
  / KB entries / Spend today); **"This week's burn" in TOKENS** linking to Spend
  (dollars `$0.00 / not metered`, Q1); **Org pulse** per-team 7-day acceptance
  table; **auto-resolution calm metric** (DERIVE, count `audit_log
  action='escalation_superseded'`); **active escalation triage list lives HERE**
  (Q2 — Home owns *active* triage, Audit owns *resolved* history); **Jobs
  awaiting-you rollup** only (Q6).
- **Deferred.** Dollar burn figure (Q1 → §6); any forecast/interpreted value (P1).

**INTERACTION SPEC.**
- **Interaction logic.**
  - **"Waiting on you" card actions** map kind→verb (e.g. *Approve merge* / *Open
    thread* / *Approve job* / *Review note*). Each primary action either (a)
    navigates to the item's real detail, or (b) for a gated op, opens the standard
    approval (it must **not** auto-execute — P1/safety; cross-ref §4.10 AC3).
    *(Prototype wires only the navigations; `Snooze / Dismiss / Triage all / Let
    agent merge` are stubs — build must wire or remove them.)*
  - **`Approve merge` vs `Open PR` must route distinctly.** Prototype bug: both go
    to the same artifact-detail stub — see §A.4 (minor).
  - Counters and the heartbeat are **live stored reads** (P1); clicking the Spend
    counter deep-links to Spend's same window (P3).
- **States + transitions.**
  - **Loading:** skeleton for heartbeat + counters + triage list.
  - **Empty/Quiet (the hero state, P2):** when the triage queue is empty, Home
    reads **"Nothing needs you right now"** — a *positive* calm state, not a blank.
    `Failure 0` renders as Quiet, never Error.
  - **Populated:** "what needs you" queue with a count badge; long tail demoted
    (2-line clamp).
  - **Error:** if a backing read fails, that card shows the honest Error state with
    retry; the rest of Home still renders (no all-or-nothing blank).
- **Keyboard/gesture.** `↑/↓` move through the triage queue, `Enter` opens the
  focused item (≤2 keys to act on the first item — supports AC5). `⌘K`/`Esc` global.
- **Edge/empty/error.** Distinguish "0 failed" (Quiet, good) from "couldn't load
  failures" (Error). Never show an urgency count the store can't substantiate.
- **Interaction acceptance.**
  - iAC1: With an empty queue, Home shows the calm Quiet state, not a spinner or
    blank. **[verifiable]**
  - iAC2: A returning founder can name the count needing them and **open the first
    item in ≤2 clicks / ≤2 keystrokes**. **[usability target]**
  - iAC3: No Home action that triggers a gated op completes without routing through
    its approval. **[verifiable, P1/safety]**

---

### 4.2 Threads (list + detail) — RENDER-ONLY

- **Purpose.** Founder-visible, multi-agent **broadcast** conversations. The sole
  collaboration surface.
- **v1 scope** (unchanged): **List** — segmented filter (`All / Waiting on you /
  Active / Done`) + counts; row leads with **last speaker**; overlapping avatar
  stack; status pills + green `live` pill. **Detail** — 2-col
  transcript + 300px rail (Participants / Linked tasks / Artifacts / stats:
  messages, **token churn**, opened). System/execution events visually distinct
  from prose (backed by `ThreadMessage.kind` + `system_payload.kind_tag`).
  **Turn budget (X/500)** visible before the cap. **Dream-origin marker** (A4)
  where applicable.
- **Deferred.** Real @mention routing (→ §6, D3); in-transcript agent-own-execution
  `ran:` cards (new store → §6, D7).

**INTERACTION SPEC.**
- **Interaction logic.**
  - **List:** click a row → that thread's detail (build routes by real `THR-id`;
    prototype routes all rows to one stub). **B.3 read-state — DEFERRED for v1
    (founder ruling, THR-010 msg 149): no new read-state store.** v1 therefore
    **omits persistent unread styling** — there is no backed per-(founder, thread)
    read/unread state to render, so the list does **not** ship the unread accent
    tint/dot as if backed (P1). Persistent unread tracking is post-v1 (§6, D11).
    Segmented filter re-queries by status; **Filter** button is a stub
    in the prototype — wire to real filters or remove.
  - **Detail:** transcript is a scrollable column of turns (founder turns styled
    distinctly); inline `TASK-*`/`JOB-*`/`PR #*` refs are click-through (P6); rail
    links open the linked task/artifact. **Composer:** text input + `Enter` to send
    + send button. *(Prototype composer is non-functional — build must wire send.)*
  - **System/dispatch events** (dispatch / participant / cap / archive / resume)
    render in a distinct system style — these are the **only** backed system events;
    do **not** synthesize agent-own `ran:` cards into the transcript (P1; D7).
- **States + transitions.** Loading (skeleton turns); **Empty** ("No threads yet" /
  per-filter "Nothing waiting on you" — calm); typing/sending → optimistic turn
  only if the send is real, else no optimism; **Error** on send failure shows the
  unsent draft + retry (never drop the founder's text silently). *(No persistent
  unread→read transition in v1 — B.3 deferred, no read-state store; §6 D11.)*
  Live thread shows the `live` pill from real state.
- **Keyboard/gesture.** List `↑/↓`+`Enter`; in detail, `Enter` sends from the
  composer, `Shift+Enter` newline; `Esc` blurs composer. `⌘K`/`Esc` global.
- **Edge/empty/error.** Empty thread (no turns yet) is calm, not an error. Turn
  budget near cap (e.g. ≥480/500) shows a non-alarming warning; **at** cap, the
  composer disables with an honest reason.
- **Interaction acceptance.**
  - iAC1: From the list, the founder can tell *who spoke last* and *whether it needs
    them* without opening the thread. **[verifiable]**
  - iAC2 (P1, **dominant here**): **No affordance implies @mention routing** the
    daemon does not perform — the composer must NOT promise "@mention an agent to
    route". **§A.2 RESOLVED** — the finalised composer reads *"Message the thread —
    all participants see it (broadcast)"* (no routing promise). **[verifiable]**
  - iAC3: System/dispatch events are visually distinct from prose. **[verifiable]**
  - iAC4: A failed send preserves the draft + offers retry. **[verifiable]**

---

### 4.3 Tasks (list + detail) — RENDER-ONLY

- **Purpose.** The org-wide work **list** (not a kanban) + a per-task
  decision/lineage surface.
- **v1 scope** (unchanged): **List** — dense 44px one-line rows; group-by `Status /
  Agent / Thread`; status groups including **Resolved (superseded)** (dimmed);
  **bidirectional lineage inline** (`↳ supersedes TASK-381` / `→ TASK-407`) backed
  by `revisit_of_task_id` + `get_direct_revisits()`. **Detail** — **connected
  vertical chain timeline** (`walk_revisit_chain()`) with node states (done /
  current-with-ring / blocked); a blocked node **names its blocker**; property rail;
  append activity log; contextual primary action. **Brief = raw monospace markdown
  + "Show full" toggle** (no slot parser; P1).

> **LOCKED TASKS INTERACTION MODEL (binding, founder ruling):** the list shows
> **ROOTS ONLY**; each root row shows a **severity rollup** of its subtree;
> **subtasks appear ONLY in the task DETAIL view (drill-in)** — there is **NO
> in-list subtask expand/collapse toggle.**

**INTERACTION SPEC.**
- **Interaction logic.**
  - **Roots-only list, no in-list subtask toggle** — **[prototype-verified]**: the
    list renders flat rows with **0** `aria-expanded` toggles and no child/disclosure
    rows (verified live: 0 expand toggles across 10 rows). Parent/child references
    in the list are **plain inline text** (`↳ supersedes …`, `→ …`), not interactive
    nesting. **Do not add a disclosure toggle** (would violate the locked model →
    §A would flag any reintroduction).
  - **Drill-in:** clicking a root row → its task detail (build routes by real
    `TASK-id`). In detail, the **parent→current→child chain** is the subtask/lineage
    surface — **[prototype-verified]**: 3 chain nodes, 1 `current` (non-navigable,
    ring), `done`/`blocked` siblings navigable (2 clickable). A blocked node names
    its blocker ("waits on TASK-349").
  - **Severity rollup on each root row** = the worst-state of that root's subtree
    (e.g. any blocked child → root shows a blocked/attention rollup pill). **The
    prototype does NOT implement this** — rows show only their own status pill (see
    §A.5). The rollup is **product intent**; it requires aggregating child/subtree
    state. **EM-validated B.1: DAEMON-BACKED (DERIVE), no schema, no new store.**
    Subtree = `parent_task_id` children (`get_children()`), **not** the
    revisit/predecessor-root chain (those are separate links); the LIST route
    computes the worst-of-subtree reduction at render/derive time over real child
    `status`. `get_children()` returns **direct** children only, so a true subtree
    rollup walks recursively (N queries or a recursive-CTE helper) — still **no
    schema change**. P1: the rollup must reflect **real** aggregated subtree state,
    never a guessed severity.
  - Group-by segmented control and **Filter** re-query/re-group; **New task**
    opens task creation. *(All three are stubs in the prototype — wire them.)*
- **States + transitions.** Loading (skeleton rows by group); **Empty** per group
  ("Nothing in review"); **Resolved/superseded** rows dimmed but present;
  selection state on `↑/↓`; row → detail on `Enter`/click. Error on a failed list
  read shows retry (not an empty list masquerading as "no tasks").
- **Keyboard/gesture.** `↑/↓` move selection, `Enter` opens, `Esc` clears. In
  detail, chain nodes are `Tab`-navigable; `Enter` follows a node.
- **Edge/empty/error.** A root with **no** subtree still shows its own status as the
  rollup (rollup of a singleton = itself). "Show full" brief toggle defaults
  collapsed for long briefs.
- **Interaction acceptance.**
  - iAC1: List is a list, not a board; **no in-list subtask toggle exists.**
    **[verifiable — prototype-verified clean]**
  - iAC2: From a root row the founder reaches the subtask chain in **1 click**
    (drill to detail); from a superseded task reaches its successor in 1 click and
    vice-versa. **[verifiable]**
  - iAC3 (P1): The severity rollup, once built, reflects **real** aggregated
    subtree state — never a guessed/estimated severity. **[verifiable; gated on §B]**
  - iAC4: A blocked task always shows *what it is blocked on* (real `blocked_on`).
    **[verifiable]**

---

### 4.4 Agents — RENDER-ONLY (wire-up) + 1 DERIVE

- **Purpose.** Editable agent roster + rich detail.
- **v1 scope** (unchanged): two-pane (roster + roomy editable detail); **editable
  system prompt, executor switch (`codex / claude / pi`), team, repo chips, tool
  chips** — RENDER-ONLY wire-up over existing write routes (`POST /agents/manage`,
  `PUT /agents/{name}/executor`, `POST /agents/{name}/repos`) — **REAL saves (Q7)**;
  **accountability metrics** ("42 tasks done · 88% accept rate") — DERIVE, **real
  counts** (P1), never estimates.
- **RULED — A1:** the **"Can act autonomously" per-agent toggle is DEFERRED, out of
  v1** (NEW-STORE + permission-model → §6, D2). **Do NOT render a non-functional
  autonomy toggle.** **§A.1 RESOLVED** — the finalised `a-agents.html` no longer
  renders any autonomy/permission toggle (agent detail = system-prompt + executor +
  Save/Reset only; zero `switch` markers, source- and playwright-verified).

**INTERACTION SPEC.**
- **Interaction logic.**
  - **Roster row click** selects the agent and loads its detail into the right pane
    (prototype selection is a stub — wire it). Per-agent **status dot** (active/idle)
    + role string is a **real stored read** (P1).
  - **Editable fields** (system prompt textarea, executor segmented, repo/tool
    chips with add/remove, `+ Add …`): edits dirty the form → **sticky save bar**
    (`Reset` / `Save agent`) appears; **Save** persists via the real route and shows
    a real success/error result; **Reset** reverts to last-saved. "Edits take effect
    on this agent's next task." *(Prototype save bar is a stub — wire to real
    routes.)*
  - **Executor switch** is a real `PUT`; reflect the persisted value on reload.
- **States + transitions.** Clean ⇄ Dirty (save bar hidden/shown); Saving → Saved
  / Save-error (honest, with retry; never a silent success). Loading skeleton for
  roster + detail. **Empty** roster ("No agents enrolled") is calm. Accountability
  metrics show a **loading** then real counts; if the DERIVE read fails → Error on
  that stat only, not a fabricated number.
- **Keyboard/gesture.** Roster `↑/↓`+`Enter`; `⌘S` saves when the form is dirty
  *(EM-validated B.6: **frontend-only** — fires the existing Save route, no new route)*; `Esc` discards focus.
- **Edge/empty/error.** Removing the last repo/tool chip is allowed but confirmed.
  Unsaved-changes guard on navigating away from a dirty form.
- **Interaction acceptance.**
  - iAC1: Changing executor/repos/system prompt from the UI **persists** (real
    route; Q7), surviving reload. **[verifiable]**
  - iAC2 (P1): Accountability metrics are real stored/derived counts, not estimates;
    a failed metric read shows Error, not 0. **[verifiable]**
  - iAC3: **No autonomy toggle is shown in v1.** **[verifiable — §A.1 RESOLVED:
    finalised design renders no autonomy toggle]**
  - iAC4: An edited system prompt is what the agent's next session receives.
    **[verifiable]**

---

### 4.5 Knowledge (KB) — RENDER-ONLY + 1 DERIVE

- **Purpose.** The org knowledge library, browsable by folder, with a
  dream-candidate review gate.
- **v1 scope** (unchanged): **List** — folder rail + stacked entry feed;
  dream-proposed entries **visually distinct** (accent moon glyph) and quarantined
  "pending review". **Detail** — fully-rendered doc with a **sticky candidate banner
  (Accept / Dismiss)** — a candidate-**status mutation** on the existing
  `dream_kb_candidates` table (`status`/`promoted_kb_slug` columns exist; new
  mutation route, **no new store, no schema**). **Real mutation (Q7).** **B.2
  "Edit first" (edit-the-candidate-body-then-accept) is DEFERRED for v1** (founder
  ruling, THR-010 msg 149 — no new stores beyond A4): v1 edits a candidate via
  **accept-then-edit-the-live-entry** using the **existing KB edit paths**, not a
  new edit-then-accept route (§6, D10).
- **RULED — K1:** usage label = **"viewed N× (CLI)"** only. **Drop "used by N
  agents" and version numbers** (the store has only a total CLI view count;
  `kb_views.view_count`; no distinct-agent counter, no version). **§A.3 RESOLVED** —
  the finalised `a-knowledge.html` now shows **"viewed 18× (CLI) · updated 2d ago"**
  / **"viewed 31× (CLI) · updated 5d ago"** ("used by N agents" and version dropped).
- **Deferred.** Citation / "load-bearing"/"uncited" badges (D5); true distinct-agent
  / version usage (D5).

**INTERACTION SPEC.**
- **Interaction logic.**
  - **Folder rail** filters the entry feed by folder (prototype folders are visual
    stubs — wire to real filtering). **Honest provenance labels** are
    **[prototype-verified GOOD]**: candidates read `from dream · proposed by <agent>
    · pending review`; the honest-label half of K1 is satisfied — keep it.
  - **Entry click** → entry detail (build routes by real slug).
  - **KB-candidate review gate (Accept / Dismiss)** — the core interaction, shared
    with §4.8, appears on **3 surfaces** (KB detail banner, Dreams panel, Dream
    detail) with consistent hierarchy (**Accept** = primary, **Dismiss** = ghost).
    **B.2 "Edit first" — DEFERRED for v1** (founder ruling, msg 149; §6 D10):
    v1 ships **no new edit-then-accept route**; the neutral "Edit first" button is
    **not** in v1. To revise a candidate, the founder **Accepts it** (it becomes a
    live entry) **then edits that live entry** via the existing KB edit paths.
    Semantics:
    - **Accept** → candidate becomes a live KB entry (`status→accepted`,
      `promoted_kb_slug` set); banner collapses to the live entry; it leaves the
      "pending" queue everywhere (shared route → consistent across surfaces).
      Once live, the entry carries the **standard "Edit" affordance** (existing KB
      edit path) for accept-then-edit — **no candidate-body edit route**.
    - **Dismiss** → `status→dismissed`; removed from the pending queue; **honest**:
      does not delete the source dream/reflection.
    - *(Both are presentational stubs in the prototype — semantics above are
      from copy + the build-spec ruling; Accept/Dismiss = a status mutation on the
      existing candidate table, no new store.)*
- **States + transitions.** Pending (candidate banner) → Accepted (live entry) /
  Dismissed (removed). Pending count tag (`2 candidates
  pending`) decrements on each resolution. Loading skeleton; **Empty** library
  ("No entries yet"); **Error** on a failed mutation re-shows the banner with retry
  (never a phantom "accepted"). *(Accept-then-edit happens on the now-live entry via
  the existing KB edit path — not a candidate "Editing" state; B.2 deferred, D10.)*
- **Keyboard/gesture.** Feed `↑/↓`+`Enter`; in the candidate banner, the two
  actions are `Tab`-ordered Accept→Dismiss; `Esc` clears selection.
- **Edge/empty/error.** Optimistic accept is allowed only if the route is real;
  on failure, revert and show Error. A candidate already resolved on another surface
  shows as resolved (shared state).
- **Interaction acceptance.**
  - iAC1: A candidate can be Accepted/Dismissed from the entry view, **Accept
    makes it live (real route)**, and the result is consistent on Dreams + Knowledge;
    revising a candidate is **accept-then-edit-the-live-entry** (existing KB edit
    path), not a candidate-body editor. **[verifiable]**
  - iAC2 (P1): Usage label reads **"viewed N× (CLI)"** — no "N agents", no version.
    **[verifiable — §A.3 RESOLVED: finalised design reads "viewed N× (CLI)"]**
  - iAC3: No citation/"load-bearing" badge ships in v1. **[verifiable —
    prototype-verified clean: no citation UI present]**

---

### 4.6 Artifacts — RENDER-ONLY

- **Purpose.** The gallery of everything agents produced, tied to the producing
  thread/agent.
- **v1 scope** (unchanged): **FLAT recency card grid** (Q4 — folder browsing
  DEFERRED); 3-col grid with type filter (`All / Pull requests / Docs / Patches /
  Designs`); each tile carries a kind pill, status tag, and **provenance** (thread +
  agent + age), limited to **stored** fields (P1).
- **Deferred.** Folder/nested-key browsing (Q4); **rich PR detail** (checks +
  files-changed + diff) — RULED A5: DEFERRED (D4).

**INTERACTION SPEC.**
- **Interaction logic.** **Flat grid, filter by kind** — **[prototype-verified]**:
  `repeat(3,1fr)` card grid, kind segmented filter, **no folder tree / no nesting**
  (matches Q4). Card click → artifact detail (build routes by real id; prototype →
  single PR stub). Kind filter re-queries (prototype filter is a stub). `[data-job]`
  refs inside an artifact are click-through to job detail (contextual; Q6-consistent).
- **States + transitions.** Loading skeleton cards; **Empty** ("No artifacts yet" —
  calm); status tags (`merged/final/applied/open/draft/v2`) are real stored states.
  A card "blocked on JOB-083" links to that job's contextual detail. **Error** on a
  failed grid read → retry.
- **Keyboard/gesture.** Grid is `Tab`/arrow navigable; `Enter` opens a card.
- **Edge/empty/error.** v1 artifact detail must **not** render a fabricated CI/checks
  panel (A5; P1) — if rich PR detail isn't stored, the detail shows only stored
  fields + an honest "no checks data" rather than fake check rows.
- **Interaction acceptance.**
  - iAC1: Every artifact shows which thread + agent produced it (stored fields).
    **[verifiable]**
  - iAC2 (P1): No PR "checks" panel renders fabricated/un-stored check states.
    **[verifiable]**
  - iAC3: Artifacts list is a **flat grid**; no folder tree in v1. **[verifiable —
    prototype-verified clean]**

---

### 4.7 Spend — RENDER-ONLY (tokens)

- **Purpose.** The **single owner** of token observability; reconciles tokens ↔
  cache; resolves cryptic model labels.
- **v1 scope** (unchanged): **TOKENS-ONLY** (Q1 — dollars render `$0.00 / not
  metered`, never a fabricated figure). Hero "this week's burn" (window toggle
  **24h / 7d / 30d**, 7d default) with **fresh-vs-cache split**; "where it went" by
  **team & agent / thread / model** (segmented); "Top threads by churn" table;
  Export. **Churn invariant [BINDING]:** `total = input + output + reasoning`; **cache
  reads in a separate column, never folded into churn or used as a ranking key.**
  **Model-label honesty** (cryptic/NULL labels render *labeled*, never blank, never
  silently "corrected").
- **Deferred.** Real-dollar cost meter (D1).

**INTERACTION SPEC.**
- **Interaction logic.**
  - **Window toggle `24h / 7d / 30d`** re-queries the spend window and **re-renders
    every card on the page to that window** (hero, breakdown, top-threads). **Persist
    the selected window** in `localStorage` (e.g. `hr-spend-window`) so it survives
    navigation — analogous to theme. *(Prototype: the toggle is a **stub**, switches
    nothing, and is **not persisted** — build must wire re-query + persistence.)*
    **EM-validated B.4: DAEMON-BACKED (render/derive), no new store.** `GET /tokens`
    accepts `since` (ISO-8601); it AND-composes into the shared filter dict applied
    to **every** aggregation — the window maps to `since = now − window` and applies
    **consistently across all breakdowns**.
  - **Breakdown segmented `Thread / Agent / Model`** re-pivots the table (stub in
    prototype). **Top-threads row** → that thread's detail / Spend-for-thread.
    **CAVEAT (separate from B.4, carry into the build):** `routes/tokens.py`
    `valid_groups` = `agent, task, failed_task, scope, thread, purpose` — there is
    **no `model` group_by** today. The `since`/window param is fully backed, but the
    **Model** pivot needs a new `model` aggregation (a DERIVE read route, no schema/
    store). Build the `model` group_by or drop the Model segment if not added.
  - **Export** triggers a real export (DERIVE route).
- **States + transitions.** Window/segment selection → loading skeleton on the
  affected cards → repopulated. **Empty/Quiet:** zero spend in a window renders
  **"No token spend in this window"** (calm), and dollars render **`$0.00 · not
  metered`** as a *real honest value* (Quiet, not Error). **Error:** a failed token
  read shows retry; never show stale numbers as current.
- **Keyboard/gesture.** Window + segment toggles are arrow-navigable button groups
  (`←/→` within the group, `Enter`/`Space` selects). `⌘K`/`Esc` global.
- **Edge/empty/error.** Cryptic model labels (`(unknown — pre-fix)`,
  `(unknown — ANOMALY)`, `(mixed)`, `(cli-unreported)`) always render the label,
  never blank, never a guessed correction. Cache-savings hero ("Cache saved 241M
  tokens · 57% from cache") uses the separate cache column (never churn).
- **Interaction acceptance.**
  - iAC1 (P1, binding): Cache reads never appear inside the churn/total number.
    **[verifiable]**
  - iAC2 (P1): Every model label is non-blank and never a fabricated correction.
    **[verifiable]**
  - iAC3 (P1): Dollars render `$0.00 / not metered` consistently across Home, Spend,
    Threads, Audit. **[verifiable]**
  - iAC4: The window toggle re-queries all cards and the choice persists across
    navigation. **[verifiable]**

---

### 4.8 Dreams — RENDER-ONLY (read) + 1 DERIVE + **A4 (the one v1 schema change)**

- **Purpose.** First-class **nightly-reflection** surface: agents reflect off the
  task clock, write learnings, propose KB candidates, open founder threads only when
  output is worth attention.
- **v1 scope** (unchanged): reflection feed + dream detail (quote → stat strip →
  narrative doc → proposed-knowledge cards → "Open reflection thread") — RENDER-ONLY;
  reflections rendered as the agent's **actual stored text** (P1); **KB-candidate
  review queue (Accept / Dismiss)** — candidate-status mutation, shared route with
  §4.5 (B.2 "Edit first" deferred → accept-then-edit-the-live-entry, §6 D10);
  **dream-originated thread marker — IN v1 (A4)** via additive nullable
  `composed_from_dream_id` on threads (the single v1 migration); **Quiet dream**
  ("nothing escalated · private learning saved") is an explicit non-alarming state.

**INTERACTION SPEC.**
- **Interaction logic.** **[prototype-verified markers]**
  - **Dream marker (A4)** = circular accent **moon** badge (crescent SVG) + italic
    display-font reflection pull-quote with an accent left-border; the same moon is
    the Dreams nav icon (with an attention dot). This marker appears wherever a
    dream-originated object shows (Dreams feed/detail, the marked thread, Home,
    Audit) — driven by `composed_from_dream_id`, **not** a UI guess.
  - **Card/candidate routing** [prototype-verified]: `.dream-card` → dream detail;
    `.kb-cand` → KB candidate detail; "Open reflection thread" → the dream's thread.
    Clicks on the Accept/Dismiss buttons are excluded from card navigation
    (button clicks don't trigger drill-in).
  - **Accept / Dismiss** — identical semantics + shared route as §4.5 (see that
    spec; B.2 "Edit first" deferred → accept-then-edit-the-live-entry). Pending
    count tag (`3 to review`) decrements on resolution.
- **States + transitions.** Per-reflection candidate states: `2 candidates` /
  `1 candidate` / **Quiet** (`no candidates · Quiet dream — nothing escalated ·
  private learning saved`) — **[prototype-verified]**, the canonical Quiet state
  (§2.5.5). Loading skeleton; **Error** on a failed candidate mutation reverts +
  retry. Dream-schedule toggles (Tonight 03:00 / Catch-up on startup) reflect real
  config.
- **Keyboard/gesture.** Feed `↑/↓`+`Enter`; candidate actions `Tab`-ordered
  Accept→Dismiss; `Esc` clears selection.
- **Edge/empty/error.** A Quiet dream is a **positive** state, never styled as
  Empty/Error. No reflections yet → calm Empty.
- **Interaction acceptance.**
  - iAC1: A dream is visually distinguishable from a coordination thread everywhere
    it appears, **driven by `composed_from_dream_id`** (A4), not a UI guess.
    **[verifiable]**
  - iAC2: Accept/Dismiss from Dreams or Knowledge yields a consistent result
    (shared real route). **[verifiable]**
  - iAC3 (P1): Reflection text shown is the agent's actual stored reflection.
    **[verifiable]**
  - iAC4 (P2): "Quiet dream" renders as a first-class positive state. **[verifiable
    — prototype-verified copy present]**

---

### 4.9 Schedule — RENDER-ONLY (read)

- **Purpose.** Give agents a working-day rhythm; make unattended scheduling visible
  and trustworthy.
- **v1 scope** (unchanged): read surface buildable now (`work_hours` route +
  `web/src/lib/api/work-hours.ts` ship on main); week grid (working-hour + dream
  bands) or per-agent 24h timeline with a "now" line + scheduled-wake dots;
  per-agent work hours; **listing of past/upcoming wakes**; a **"While you were
  away"** wake feed; **behavior toggles** (Finish in-flight work / Hold escalations
  / Urgent override) surfaced from existing Org settings.
- **Deferred.** **Creating/editing named recurring "wakes"** as first-class editable
  objects (D6 — store records wake-*executions*, not editable wake *definitions*).

**INTERACTION SPEC.**
- **Interaction logic.** **[prototype-verified]** the week grid is **render-only**:
  an 8-row × 7-day grid procedurally drawn with a working-hours band (weekdays
  09:00–18:00) and a dream-window band (03:00). **There is NO drag/click editing**
  despite copy implying "you decide the shape of the week" — v1 **views**, it does
  not author. The behavior toggles + "Save schedule" are **stubs** in the prototype;
  if they map to real Org-settings writes, wire them (Q7); otherwise render
  read-only.
- **States + transitions.** Loading skeleton grid; weekend cells = `paused` (Quiet,
  not Error); "now" line reflects real time; wake dots are real scheduled/executed
  wakes. **Error** on a failed scheduler read → retry. The "While you were away"
  feed Empty state ("Nothing ran while you were away") is calm.
- **Keyboard/gesture.** Grid is informational; the wake list is `↑/↓`+`Enter`
  navigable to a wake's detail. Toggles `Space`/`Enter`.
- **Edge/empty/error.** **No affordance may imply you can create a new named
  recurring wake** in v1 (D6). Editing copy that promises authoring must be softened
  to "view".
- **Interaction acceptance.**
  - iAC1: Founder can **view** per-agent work-hours + past/upcoming wakes (read
    parity with the `work-hours` CLI). **[verifiable]**
  - iAC2 (P1): Schedule reflects the daemon's actual scheduler state, not a mock.
    **[verifiable]**
  - iAC3: No UI affordance implies creating a new named recurring wake in v1.
    **[verifiable]**

---

### 4.10 Assistant (dock, not a page) — reference-existing-ID deep-links only

- **Purpose.** A **conversational operator** that both answers and acts on the
  runtime — "an assistant that runs your runtime, not a terminal."
- **PRODUCT INTENT (RULED — A3)** unchanged: omnipresent **dock** (⌘K / pill; Esc
  closes; persists across nav); inline **`ran: <cmd>` transparency cards** (real);
  the current `/assistant` xterm becomes the dock header's **"Open full
  session"** escape hatch.
- **IMPLEMENTATION (OPTION 3, founder-ruled):** hybrid dock with zero
  frozen-contract change. The server output pump starts raw immediately (legacy
  xterm path byte-identical); the structured frontend tolerates/buffers any raw
  PTY frame received before its handshake ack, so the user never sees a raw
  frame. No change to connect/upgrade/endpoint/auth/bearer-subprotocol/handshake/
  PTY-attach. **Action chips are reference-existing-ID deep-links only** — each
  chip navigates to the object's existing approval or detail surface. No
  propose-new-action chip; no self-approve/self-execute path (TASK-414 guardrail).
  See `docs/agent-guides/features-and-invariants.md` (System assistant section)
  for the definitive invariant.
- **BUILD APPROACH** (xterm-on-top vs separate React component) is **EM's
  feasibility spike TASK-414's call** — this PRD specifies product intent only.

**INTERACTION SPEC.**
- **Interaction logic.** **[prototype-verified core]** (see §2.5.2 for the canonical
  open/close/focus spec). Composer accepts text + `/` to run a command; `Enter`
  sends. **Action chips** ("Approve JOB-083", "Open THR-021", "Show the diff")
  are **reference-existing-ID deep-links only** — each chip navigates to the
  object's existing approval or detail surface (e.g. "Open THR-021" opens the
  thread detail page, "Approve JOB-083" opens the job review gate). No
  propose-new-action chip; no self-approve/self-execute path (TASK-414
  guardrail, consistent with §1.2).
  - **`ran:` cards** show the verbatim command + churn/cache/model where relevant —
    real, because the dock executes real commands.
- **States + transitions.** Closed (default, `pointer-events:none`) ⇄ Open (focus
  in composer). Sending → assistant thinking → response + any `ran:` card. **Error**
  (command failed) shows the real failure verbatim, never a fabricated success.
  **Empty** (fresh dock) shows a calm prompt, not fake history.
- **Keyboard/gesture.** `⌘K` toggle, `Esc` close (**[prototype-verified]**),
  `Enter` send, `/` command mode. **Build must add focus-trap + focus-restore**
  (§2.5.2). **EM-validated B.5: frontend-only deep-links, NO new privileged path.**
  Action chips are reference-existing-ID deep-links only — each chip navigates to
  the object's existing surface (no POST, no job submission, no executable payload).
  The TASK-414 guardrail holds: the assistant NEVER self-approves or self-executes
  a privileged op. (Chips are net-new frontend on today's PTY-attach assistant.)
- **Edge/empty/error.** A chip whose underlying op is **not** already gated must not
  exist in v1 (→ §6 if ever proposed). "Open full session" reaches the retained
  xterm.
- **Interaction acceptance.**
  - iAC1: `⌘K` opens the dock from every surface; `Esc` closes; state persists.
    **[verifiable — open/close prototype-verified; persist-across-nav is a build req]**
  - iAC2 (P1): Any command the assistant runs is shown verbatim. **[verifiable]**
  - iAC3 (P1/safety): An assistant action requiring founder approval routes through
    the standard gate; it never auto-executes a protocol edit / merge. **[verifiable]**
  - iAC4: "Open full session" reaches the retained xterm. **[verifiable]**
  - iAC5 (a11y): Opening traps focus in the dock; closing restores focus to the
    trigger. **[verifiable — net-new vs prototype]**

---

### 4.11 Settings (page, not dialog) — RENDER-ONLY

- **Purpose.** The in-app configuration surface.
- **v1 scope** (unchanged): dedicated **page** with sticky left sub-nav (`Assistant ·
  System · Organization · Agents · Executors · Usage`) + field panel + sticky save
  bar; real bookmarkable **`/settings` route**; **Org section editable** (already
  shipped #102) — **real saves (Q7)**; **per-field live-vs-restart labeling**
  matching actual daemon behavior (P1); **agent-name chips with autocomplete**;
  System Assistant config (status, Init/Repair, "Open terminal") integrating §4.10.
- **Deferred.** Agents+Settings unification (non-blocking); Usage stays
  tokens-only until the cost meter is ruled in (D1).

**INTERACTION SPEC.**
- **Interaction logic.**
  - **Sub-nav** switches the field panel (real routing to `/settings/<section>`).
    *(Prototype sub-nav items are stubs — no panel switch; wire them.)*
  - **Segmented controls** (Assistant surface `Dock|Palette|Full page`; executor
    `codex|claude|pi`; default org) and **switches** select on click and **dirty the
    form**; **sticky save bar** (`Discard` / `Save changes`) commits via the real
    route. *(Prototype controls are frozen `.on` states — wire selection + save.)*
  - **Per-field live-vs-restart badge** is **honest**: a field tagged "restart to
    apply" must actually require a restart (the prototype already shows a "restart to
    apply" pill on "Default org for new threads" — keep this pattern, ensure it
    matches real daemon behavior).
  - **Agent-name inputs autocomplete from the real roster** (no free-text typos).
- **States + transitions.** Clean ⇄ Dirty (save bar). Saving → Saved / Save-error
  (honest; no silent success). Validation errors render inline. Loading skeleton per
  panel.
- **Keyboard/gesture.** `Tab` through fields; segmented groups arrow-navigable;
  `⌘S` saves when dirty; unsaved-changes guard on nav-away.
- **Edge/empty/error.** **§A.2 RESOLVED:** the Settings **"Founder handle"** field
  in the finalised design now reads *"The handle agents reference when they broadcast
  to you."* — broadcast framing, **no** "route questions to you" routing promise. The
  required A2 rewording is already in the finalised design; keep this broadcast copy.
- **Interaction acceptance.**
  - iAC1: `/settings` is a real bookmarkable route; sub-nav switches panels.
    **[verifiable]**
  - iAC2 (P1): Every field is correctly labeled live-apply vs restart-required,
    matching real daemon behavior. **[verifiable]**
  - iAC3: Agent-name inputs autocomplete from the real roster. **[verifiable]**
  - iAC4 (P1): No field implies @mention routing the daemon doesn't perform
    (§A.2). **[verifiable]**

---

### 4.12 Audit — RENDER-ONLY + 1 DERIVE

- **Purpose.** The immutable, append-only forensic record — "what happened, who,
  when" — exportable. **Owner of the resolved-escalation history** (Q2) and
  **completed/past jobs** (Q6).
- **v1 scope** (unchanged): day-grouped timeline; color-coded event classes
  (completed / merge / escalation / failure); every entry carries **executor +
  token cost (tokens, not dollars)**; an **Event-types legend with counts** that
  doubles as a filter; **Export** (DERIVE); **"Failure 0" is a first-class calm
  state** (P2).
- **Deferred.** Per-event real-dollar cost (D1); full **query DSL** (D9 — v1 ships
  the legend-as-filter only).

**INTERACTION SPEC.**
- **Interaction logic.** **[prototype-verified history framing]**: append-only,
  reverse-chronological, day-grouped, immutable/exportable — reads as **history**,
  distinct from Home's active triage (Q2 ownership is clean). Timeline events carry
  colored class dots (ok / warn / merge). **Event-types legend rows act as filters**
  (click a type → filter the timeline; prototype rows have `cursor:pointer` but **no
  handler** — wire them). **Export** triggers a real export. Inline IDs are
  click-through (P6).
- **States + transitions.** Loading skeleton timeline; **filter applied** → subset;
  **Empty filter result** ("No merge events in this range" — calm); **`Failure 0`**
  renders as **Quiet** (good), never Error. A failed audit read → Error + retry.
- **Keyboard/gesture.** Timeline `↑/↓`+`Enter` to open an event's target; legend
  filters are `Tab`/`Enter` togglable. `⌘K`/`Esc` global.
- **Edge/empty/error.** The mono "query" examples (`actor:dev_agent`,
  `action:merge since:7d`) are **display-only** in v1 — they must **not** look like a
  working input unless the DSL is built (D9). Render them as legend hints, not an
  active query box.
- **Interaction acceptance.**
  - iAC1: Every audit line is immutable, attributable (actor), exportable.
    **[verifiable]**
  - iAC2: Filtering by event type / actor / time maps to stored events (P1).
    **[verifiable]**
  - iAC3: Resolved escalations + completed jobs appear in Audit; active escalations
    on Home; no item double-owned (Q2/Q6). **[verifiable]**
  - iAC4: The query-DSL hint is not presented as a working input in v1 (unless EM
    scopes D9 in). **[verifiable]**

---

### 4.13 Jobs — NO standalone surface (retired); RENDER-ONLY job *detail* + 1 DERIVE

> **SUPERSEDED 2026-06-26 by founder ruling (THR-030 seq 91, TASK-907): the standalone Jobs surface is reinstated as the approval queue.**

- **RULED — Q6.** The standalone **Jobs tab is retired** **[prototype nav-verified:
  no Jobs nav item]**. Jobs are: **rolled up on Home** (awaiting-you; §4.1),
  **historical in Audit** (§4.12), **reachable contextually** from the spawning
  thread/task (job-detail breadcrumb "Back to THR-021").
- **Job detail (v1 scope, unchanged):** **verbatim command** (`script_text` +
  interpreter + cwd_hint) — RENDER-ONLY; **"If approved" cascade** — DERIVE
  (`blocked_on_job_ids` + `list_tasks_blocked_on_jobs()`; **must reflect real
  downstream tasks**, no invented effects, P1); **honest attention signal + uniform
  two-step confirm** ("🔑 needs credential" / "flagged for review" — **NO danger-tier
  ranking**; only `review_required` exists); **real approval (Q7)** where the route
  exists.
- **Deferred.** Stored diff preview (D8); any Jobs index/list page (retired).

**INTERACTION SPEC.**
- **Interaction logic.** Job detail is reached **only contextually** — **[prototype
  partially-verified]**: it is **not** in nav (good, Q6), but it is deep-linked from
  artifact-detail (`[data-job]`), artifact cards ("blocked on JOB-083"), Audit
  events, and the assistant "Approve JOB-083" chip. This **matches** Q6 (jobs are
  thread/task-anchored, reachable contextually) — **not a delta**. **One IA bug to
  fix:** the job-detail page sets `data-active="threads"` (masquerades as Threads in
  the nav) — it should highlight the contextual origin or no nav item (§A.4, minor).
  - **Approve job** → **uniform two-step confirm** (not a danger-tier ladder) →
    real approval route (Q7). **Decline & revert** → its real path. **Ask the
    assistant** → opens the dock pre-scoped to this job.
  - **"If approved" cascade** lists the **real** downstream tasks that unblock — no
    invented effects (P1; DERIVE).
- **States + transitions.** `awaiting your approval` (warn) → Approved / Declined.
  Protocol-change jobs show the honest "needs your sign-off" banner. Loading
  skeleton; **Error** on a failed approval → retry, never a phantom approval.
- **Keyboard/gesture.** Confirm dialog is `Enter`-to-confirm / `Esc`-to-cancel; the
  two-step confirm requires an explicit second action (no single-key destructive
  approve).
- **Edge/empty/error.** **No danger-tier ranking** — uniform confirm regardless of
  op (**[prototype-verified clean: priority is a neutral tag, no tier ladder]**). No
  stored diff is shown (D8) — the command text is the source of truth, not a
  fabricated diff.
- **Interaction acceptance.**
  - iAC1: Job approval shows the real command + real downstream-impact list (no
    invented effects — P1). **[verifiable]**
  - iAC2: No danger-tier ranking; **uniform two-step confirm**. **[verifiable —
    prototype-verified clean]**
  - iAC3: No standalone Jobs tab/index exists; jobs reachable via Home rollup, Audit
    history, thread/task context. **[verifiable — prototype nav-verified]**

---

## 5. The "no" list (cuts — re-proposing needs a Founder reversal) — unchanged

Jobs danger tiers / target chips / progress bars / "Effect" line / dry-run preview ·
job-detail **stored diff** · agent **failure-pattern psychoanalysis** + invented
clusters · KB **citation badges** / "load-bearing" sort · KB **"used by N agents ·
v3"** (→ "viewed N× (CLI)", K1) · Tasks **board/kanban** · Tasks **8-slot brief
parser** · **real-dollar figures** on any v1 surface (`$0.00 / not metered`, Q1) ·
**@mention routing** affordance (broadcast-only, A2) · agent **autonomy toggle**
rendered as functional (A1) · in-transcript **agent-own `ran:` cards** in Threads
(D7) · Direction B "Mission Control" · reintroducing **Talks**.

> **The finalised design now CONFORMS to all three of these cuts** (autonomy toggle,
> @mention routing copy, KB "used by N agents · v3") — the §A deltas flagged in the
> 2026-06-16 reconciliation are **RESOLVED in the finalised design**. The cuts remain
> cuts (the design conforming does not reopen them). See §A.

---

## 6. Deferred / post-v1 (FOUNDER-GATED) — D1–D11

D1 real-dollar cost meter (Q1/A6) · D2 agent autonomy toggle (A1) · D3 @mention
routing (A2) · D4 Artifacts↔PR/CI/review/job linkage (A5) · D5 KB rich usage +
citation badges · D6 editable named-recurring-wake definitions · D7 in-thread
agent-own `ran:` cards · D8 job-detail stored diff · D9 Audit query DSL ·
**D10 KB candidate edit-then-accept route** (B.2 "Edit first" — edit the candidate
body *before* accepting; deferred per founder msg 149. v1 uses
accept-then-edit-the-live-entry via existing KB edit paths — **no new store/route**) ·
**D11 thread per-(founder, thread) read-state store** (B.3 persistent unread/read;
deferred per founder msg 149 — **no new read-state store** in v1; v1 omits persistent
unread styling). **The one schema change promoted INTO v1: A4
`composed_from_dream_id`** (additive, nullable, no backfill). **No new store for v1
beyond A4** (deferred set: D1 dollar meter, D2 autonomy toggle, D3 @mention routing,
D4 artifact↔PR, D5 KB rich-usage, D10 B.2 edit-route, D11 B.3 read-state).

---

## 7. Honesty-tier sequencing (engineering alignment — NOT a timeline) — unchanged

1. **Foundation first — IA-1 shell + IA-2 routing** (+ the §2.5 global interaction
   model: shell, ⌘K dock, theme, state vocabulary — everything hangs off these).
2. **Render-only surfaces in parallel** (the large majority; no data risk).
3. **DERIVE items alongside** (each a new read/aggregation route, no schema, in EM's
   authority): auto-resolution metric, agent accountability metrics, **Tasks
   severity rollup** (B.1 DAEMON-BACKED), KB/Dreams candidate **Accept/Dismiss**
   status-mutation route (B.2 "Edit first" deferred → D10), Jobs "if-approved"
   cascade, Audit export, upcoming-wakes listing, Spend-window re-query (B.4; Model
   pivot needs a new `model` group_by).
4. **A4 (`composed_from_dream_id`)** — the single v1 migration; sequence with Dreams
   + Threads.
5. **Hold every §6 item behind its founder ruling.**

---

## 8. Measurable success criteria (org-level) — unchanged + interaction adds

- **Calm:** founder answers "is anything on fire?" in one glance on Home; empty
  queue reads as the positive Quiet state, not a blank. (Dogfood.)
- **Homes for the homeless:** Spend, Dreams, Schedule each reachable in 1 click.
- **Honesty (dominant):** zero UI elements (data **or interaction**) assert facts
  the daemon can't substantiate; dollars `$0.00 / not metered`; @mention
  broadcast-only; KB "viewed N× (CLI)"; no autonomy toggle. (P1 review gate.)
- **Cost legibility:** "what did this week cost (tokens), on what?" on one surface
  (Spend), window-persistent.
- **Time-to-decision:** a founder-gated approval reachable + actionable in ≤2 clicks
  / ≤2 keystrokes from Home. (Dogfood.)
- **Statefulness (Q7):** approvals + edits are **real saves** wherever a route
  exists; failures show honest Error + retry, never phantom success.
- **Interaction integrity (NEW):** every surface implements the four-state vocabulary
  (loading/empty/quiet/error) honestly; ⌘K dock + theme persistence behave per
  §2.5; lists are keyboard-navigable.

---

## A. Design-vs-locked-decision DELTAS — **FOUNDER DECISION REQUIRED**

> Where a Direction A design **contradicts a locked founder ruling**, it is surfaced
> here rather than silently overridden. **The PRD body specs the LOCKED decision.**
> As of the **2026-06-17 reconciliation against the finalised design, all three
> deltas are RESOLVED** — the finalised design removed/reworded each to match the
> locked decision (source- and playwright-verified, §C). They are **retained here
> (struck through) for the audit trail**; none requires a founder ruling now.
> Borderline/non-deltas are listed in §A.4.

**A.0 — Reconciliation status (READ FIRST).** ✅ **The TASK-457 stale-design caveat
is RESOLVED.** The founder supplied a working handoff link (msg 140); the **fresh
fetch SUCCEEDED** (§C.1) and this reconciliation is against the **real finalised
Direction A design**, not the older captured bundle. Outcome: **A.1, A.2, A.3 →
RESOLVED** (the finalised design conforms to the locked decision in each case);
**no new locked-decision deltas** surfaced from a full file-level diff of every
`a-*.html` surface + `shell.js` + `ds.css`. The finalised design is in fact
**materially more honesty-aligned** than the 2026-06-16 bundle (§A.5).

| # | Locked ruling | Finalised-design status (verified 2026-06-17) | Disposition |
|---|---|---|---|
| **A.1** | **A1** — autonomy toggle DEFERRED, do **not** render | ✅ **RESOLVED.** `a-agents.html` renders **no** autonomy/permission toggle — agent detail is system-prompt + executor segment + Save/Reset only (zero `switch` markers; source + playwright). *(Was: a "Can act autonomously" switch default-ON in the 06-16 bundle.)* | **Dropped from active deltas.** Build the locked decision (no toggle); the design already conforms. Real autonomy stays deferred (D2). |
| **A.2** | **A2** — broadcast-only; no @mention routing affordance | ✅ **RESOLVED.** Composer placeholder now **"Message the thread — all participants see it (broadcast)"**; Settings "Founder handle" now **"The handle agents reference when they broadcast to you."** Neither promises routing. *(Was: "…@mention an agent…" + "route questions to you".)* | **Dropped from active deltas.** Keep the broadcast copy. Real routing stays deferred (D3). |
| **A.3** | **K1** — KB usage label = "viewed N× (CLI)"; drop "used by N agents" + version | ✅ **RESOLVED.** `a-knowledge.html` now shows **"viewed 18× (CLI) · updated 2d ago"** / **"viewed 31× (CLI) · updated 5d ago"** — no "used by N agents", no version. Honest candidate labels ("from dream · proposed by … · pending review") retained. *(Was: "used by 4 agents · updated 2d ago · v3".)* | **Dropped from active deltas.** The locked honest label is already in the design. |

> **No active §A deltas remain.** Nothing in the finalised design requires a founder
> decision on a locked-ruling contradiction. (Two non-blocking refinement/minor items
> persist — see §A.4.)

**A.4 — Refinements / minor items (no founder ruling required).**
- **`$0.00` / churn "not metered" captions — NOW BUILT INTO THE DESIGN ✅.** The
  finalised design carries the **"not metered"** caption on every dollar/churn figure:
  Spend (`$0.00` + "not metered"), Dashboard ("9 active threads · not metered"), the
  ⌘K dock (`shell.js`: "Cost / not metered"), thread rail ("715K churn · not
  metered"), Assistant ("Cost / not metered"). The 06-16 A.4 refinement (the dock's
  bare "$0.00" needed the caption) is **resolved in the design**. Consistent with Q1.
- **Job concept woven through Artifacts/Audit/dock + `a-job-detail.html`** —
  consistent with Q6 (Jobs tab retired; job detail reachable contextually). **Not a
  delta.** **Minor IA bug PERSISTS:** `a-job-detail.html` still sets
  `data-active="threads"` (nav masquerade) — fix to reflect contextual origin. (Carry
  into the build; not a founder item.)
- **Tasks "Approve merge" / "Open PR" route to one stub** — prototype shortcut; build
  routes by real ID/action. Minor.

**A.5 — Honesty improvements added by the finalised design (informational).**
- **Tasks severity rollup NOW PRESENT** as **honest subtask counts** ("1 of 2
  subtasks blocked", "1 subtask running", "1 subtask in review", "2 subtasks · done")
  — the 06-16 "rollup absent" gap is closed, and rendered as countable facts, **not**
  a synthesized worst-severity badge. EM feasibility (subtree queryability) still
  open → §B.1.
- **Cache reads separated from churn** in the Spend burn chart (distinct `.bar.cache`
  bars) — matches P7 (cache never folded into churn).
- **A3 affirmed in copy:** Assistant — *"I can propose these actions — you approve
  each one. I never self-approve a merge or protocol edit."*; Job-detail — *"Every
  gated action uses the same two-step confirm — no risk tiers."*

---

## B. EM feasibility validation register — **RESOLVED (folded into the surface specs)**

> **All §B items are validated and folded in** (engineering_manager, TASK-461;
> verdicts code-grounded against `origin/main`, TASK-459). No `[needs EM feasibility
> validation]` tags remain in the surface specs. **B.1 / B.4 / B.5 = DAEMON-BACKED**
> (render-only/derive against existing data + routes; no new store). **B.2 / B.3 =
> DEFERRED for v1** per founder ruling (THR-010 msg 149 — **no new stores for v1
> beyond A4**); both moved to §6 (D10/D11). **B.6 = frontend-only.** No item blocks
> the PRD; none requires a new store.

| # | Surface | Interaction | Verdict (TASK-461) |
|---|---|---|---|
| **B.1** | Tasks (§4.3) | **Severity rollup** on each root row = worst-of-subtree | **DAEMON-BACKED (DERIVE), no schema/store.** Subtree = `parent_task_id` children (`get_children()`), **not** the revisit chain; LIST route reduces real child `status` to worst-of at render/derive time (recursive walk for a true subtree). Folded into §4.3. |
| **B.2** | Knowledge / Dreams (§4.5/4.8) | **"Edit first"** on a KB candidate | **DEFERRED for v1 (no new store, no new edit-then-accept route).** v1 ships **Accept / Dismiss** (status mutation on the existing `dream_kb_candidates` table) + **accept-then-edit-the-live-entry** via the **existing KB edit paths**. The edit-the-candidate-body-before-accepting route is post-v1 (§6 D10). Folded into §4.5/§4.8. |
| **B.3** | Threads (§4.2) | **Unread→read** clears on open | **DEFERRED for v1 (no new read-state store).** No backed per-(founder, thread) read/unread state exists; v1 **omits persistent unread styling** rather than render it as if backed (P1). Persistent unread tracking is post-v1 (§6 D11). Folded into §4.2. |
| **B.4** | Spend (§4.7) | **Window toggle** (24h/7d/30d) re-queries all cards | **DAEMON-BACKED (render/derive), no store.** `GET /tokens` `since` (ISO-8601) AND-composes across every aggregation; window = `since = now − window`, consistent across breakdowns. **Caveat:** no `model` group_by exists yet (`valid_groups` lacks `model`) — the **Model** pivot needs a new aggregation (DERIVE, no schema) or drop it. Folded into §4.7. |
| **B.5** | Assistant (§4.10) | **Reference-existing-ID deep-link chips** | **FRONTEND-ONLY, NO new privileged path.** Each chip navigates to the object's existing approval or detail surface — no POST, no job submission, no executable payload. TASK-414 guardrail upheld — the assistant never self-approves or self-executes. Folded into §4.10. |
| **B.6** | Settings/Agents (§4.4/4.11) | **`⌘S` save**, unsaved-changes guard, list keyboard nav, dock **focus-trap/restore** | **Frontend-only — clears.** No daemon dependency; touches no route (⌘S fires the existing Save route). Folded into §2.5.2 / §2.5.6 / §4.4. |

> B.1/B.4/B.5 are DAEMON-BACKED; B.2/B.3 are DEFERRED (no new store); B.6 is
> frontend-only. **No new store is introduced for v1 beyond A4.**

---

## C. Verification appendix (what was actually done)

### C.1 Design fetch — ✅ **SUCCEEDED this round (2026-06-17, TASK-460)**
- **Working URL** (founder msg 140)
  `https://api.anthropic.com/v1/design/h/yBzJDjf0BfkU2w0BExwdxg?open_file=HappyRanch+-+Direction+A.html`:
  - `curl` → **HTTP 200**, `application/gzip`, **8,468,067 bytes** (gzip→tar per
    LRN-001). Decompressed → `happyranch/` with README + 10 chat transcripts +
    `project/screens/a-*.html` (the real finalised Direction A surfaces) + `shell.js`
    + `ds.css`. Studied in full.
  - **Distinct from the 06-16 bundle** (8,332,847 B) — a genuinely newer export; every
    `a-*.html` surface, `shell.js`, and `ds.css` differs.
- **Captured to shared artifacts as the new canonical source:**
  `product_lead-2026-06-17-design-overhaul-direction-a-FINAL-fetched-bundle.tar.gz`
  (supersedes `design-overhaul-direction-a-bundle.tar.gz`).
- **The TASK-457 stale-design caveat is RESOLVED** — this PRD is reconciled against
  the real finalised design. (Prior round, the then-fresh URL `ErkhcqawVrRWfdEH1jk-Mg`
  had 404'd and DesignSync was login-blocked; LRN-001 documents that handoff URLs
  expire — this new URL works as of 2026-06-17.)

### C.2 Playwright — **SUCCEEDED** (served the finalised bundle over `http://localhost:8771`; `file://` is blocked)
Re-verified against the **finalised** prototype (2026-06-17):
- **⌘K assistant dock:** `⌘K` opens (`.hr-assist.open=true`) and **focus moves to the
  composer input** (placeholder "Ask the assistant, or type / to run a command…");
  **`Esc` closes** (`.open=false`). ✅ (Focus-trap/restore still absent → build
  requirement, §2.5.2.) **Unchanged.**
- **Theme persistence (P5):** toggled dark → `localStorage['hr-theme']='dark'` →
  navigated Tasks→Dashboard → **still dark** (`data-theme='dark'`). ✅ **Unchanged.**
- **Tasks roots-only / no in-list toggle:** **0** `aria-expanded` toggles. ✅
  **Unchanged.**
- **Tasks severity rollup — NOW PRESENT (CHANGE):** **5** `.rollup` badges with honest
  subtask counts ("1 of 2 subtasks blocked", "1 subtask running", "1 subtask in
  review", "2 subtasks · done"). The 06-16 prototype had **none** (§A.5, §B.1). ✅
- **Tasks drill-in chain:** task detail shows **3 chain nodes** — `.done[data-task]`
  (TASK-340), `.current` (TASK-349, non-navigable "you · now"), `[data-task]`
  (TASK-351, blocked). 2 carry `data-task` drill-in markers. ✅ **Unchanged.**
- **KB candidate Accept/Edit/Dismiss:** present on `a-knowledge-detail.html`
  (Accept=primary, Edit first=neutral, Dismiss=ghost) in the prototype. ✅
  **(v1 disposition: the "Edit first" button is DEFERRED — B.2, §6 D10; v1 ships
  Accept/Dismiss + accept-then-edit-the-live-entry.)**
- **Spend window toggle:** `24h / 7d / 30d` (7d active) + breakdown `Thread / Agent /
  Model`; "not metered" present; **7** separate `.bar.cache` bars (cache ≠ churn). ✅
- **Resolved deltas (re-verified):** **no** autonomy switch on Agents (§A.1); composer
  "Message the thread … (broadcast)" + Settings handle "…broadcast to you" (§A.2); KB
  "viewed N× (CLI)" (§A.3). ✅
- Page loads clean (0 console errors on the screens checked).

### C.3 Artifacts captured / current this task (TASK-460)
- `product_lead-2026-06-17-design-overhaul-direction-a-FINAL-fetched-bundle.tar.gz`
  — **the new canonical design source** (finalised design, fetched + uploaded this task).
- `product_lead-2026-06-17-design-overhaul-PRD-final.md` (this document — reconciled,
  overwritten).
- Prior-round evidence PNGs (`…-final-dock-open.png`, `…-final-agents-autonomy.png`,
  `…-final-kb-usage-label.png`) remain in artifacts; the agents-autonomy + kb-usage
  shots now document the **superseded** 06-16 state (deltas since RESOLVED).

---

*End of finalised PRD. Inputs: `product_lead-2026-06-16-design-overhaul-PRD-build-spec.md`
(TASK-415) + `engineering_manager-2026-06-16-design-overhaul-gap-analysis-validated.md`
(TASK-413, `origin/main @ 77150e0`) + **the finalised Direction A design fetched
2026-06-17** (`…-direction-a-FINAL-fetched-bundle.tar.gz`, source- + playwright-verified;
supersedes the 2026-06-16 captured bundle). Rulings per founder msgs 62/66/111/140
(THR-010). Scope + interaction specs locked; all §A deltas RESOLVED against the
finalised design; no new deltas. **§B feasibility register RESOLVED and folded into
the surface specs** by EM (TASK-459 validation, TASK-461 fold + land; code-grounded
against `origin/main`): B.1/B.4/B.5 DAEMON-BACKED, B.2/B.3 DEFERRED (no new store;
founder msg 149), B.6 frontend-only. EM owns + landed this on the `design-overhaul`
branch as the canonical build spec. — product_lead, TASK-457 (finalised) / TASK-460
(reconciled), 2026-06-17; §B folded + landed by engineering_manager, TASK-461,
2026-06-17.*
