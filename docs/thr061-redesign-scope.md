# THR-061 — UI Redesign (Direction A): Intended Change Scope

**Umbrella tracking doc** for the `feat/thr061-ui-redesign → main` redesign.
**Authority:** `engineering_manager/2026-07-07-thr061-design-change-analysis.md` (TASK-2198)
**Base:** `feat/thr061-ui-redesign` @ `e2bf7032` (== `main` HEAD; empty pointer, no dev started).
**Task:** TASK-2214 (supersedes cancelled TASK-2208). Founder authorization: THR-061 seq85.

This document is the **contract for what this branch will and will not change**, published up front so the founder can review scope before any slice is built. Each slice below lands as its own PR **into `feat/thr061-ui-redesign`** via the standing FE workflow; the umbrella PR (`feat → main`) stays **DRAFT** until every slice has landed and is then surfaced for founder sign-off (a breadth change — not auto-merged).

---

## A. In scope — presentation slices (per authority §5)

All slices are **PRESENTATION-only** and land on already-Direction-A foundations (the palette is already ours — Pasture/THR-030). Each runs the **STANDING FE WORKFLOW** (MEM-088): `frontend_engineer` (Step-0 reconciliation → screenshot-diff vs the named mockup) → `code_reviewer` APPROVE → `qa_engineer` PASS → **EM merge** on APPROVE + PASS + CI 4/4 (Web job green, not just vitest — MEM-054) + CLEAN.

**Slice 0 merges FIRST and blocks all others.** Slices 1–12 touch disjoint feature dirs and fan out (width ≤ 8; per-PR merge is the control).

| # | Slice | Mockup(s) | Scope (presentation-only) |
|---|---|---|---|
| **0** | **Token foundation** (blocks all) | `ds.css` | Diff `ds.css` → `tokens.css`; add missing **name-aliases only**; **zero new raw values**; regenerate `registry.json` (MEM-126); pass hex gate (MEM-073). No visual change expected. |
| 1 | Home / Dashboard | a-dashboard | Flavor-tinted escalation chips (real `flavor`), **client-derived** "stale 24h+" from `age_seconds`, heartbeat/org-pulse/activity restyle. Keep roots-only. Drop $-today line. |
| 2a | Threads list | a-threads | Full-width single-column list, segmented All/Open/Archived pill, LED status dots, dream-tag. |
| 2b | Thread detail | a-thread-detail | Rail + responder-strip + decline-row + system-divider restyle. **Thread-tasks rail = STATUS-PILL + ID** (founder seq79 — overrides the artifact's id-only note). Omit per-thread "715K tokens" unless a real field is confirmed. Escalation buttons stay **navigation-only**. |
| 3a | Tasks list | a-tasks | Grouped-card restyle. Render Filter / New-task **disabled/omitted** (gated). Keep count-free rollup. |
| 3b | Task detail (+ fanout restyle) | a-task-detail, a-task-detail-fanout | Approve/Reject inline panels (same `resolve-escalation`), add `Block kind` rail row. Fan-out: restyle **`running`/`joined` only**. **DROP** the pending/approval band + per-child tokens/locale/progress/artifact-link/executor. |
| 4 | Agents | a-agents | Roster + detail restyle. **Keep** the shipped model control + 4-executor set. Name/role/system-prompt/tools stay **read-only**. **DROP** "88% accept rate", active/idle status, prose roles. |
| 5 | Knowledge (list + detail) | a-knowledge(+detail) | List restyle. **DROP** tags-filter, "From dreams" origin facet, "from dream/proposed by/pending" badges on live entries, revision-history, "Edit first". |
| 6 | **Usage** (rename Spend→Usage) | a-spend | Rename `PageHeader`/route/file **Spend → Usage** (**keep `/spend` redirect**), two-pane hero, composition bar + `total = input+output+reasoning`, cache split, category restyle. **Keep** `$0.00 · not metered` fence. **Omit** per-day burn chart (gated). |
| 7 | Jobs (list + detail) | a-jobs, a-job-detail | Status-group headers w/ dot+count; command + property-rail restyle. Omit #204 credential tag / #206 approval rail (gated). |
| 8 | Artifacts list | a-artifacts | Optional name-derived folder tree + breadcrumb (additive) + card restyle. **Keep current PR/design-aware FILTERS.** |
| 9 | Work Hours polish | a-workhours | Restyle-only pass to close screenshot-diff gaps. Routine-task editor stays read-only. |
| 10 | **Runtime Health (NEW page)** | a-health | New `/health` page + web `metrics.ts` client/hook against existing `/api/v1/metrics` + `/metrics/history` (**no new daemon route**). Cards + loop/HTTP tables + history charts. Satisfies #302. |
| 11 | Onboarding shell (NEW, partial) | a-onboarding | Welcome/create/success + broken-org list (backed today). **Gate/defer** template picker, broken-Retry, executor-prereqs. Presentational shell only. |
| 12 *(optional)* | Assistant dock placement | a-assistant | **Only if founder wants** center-float vs the shipped right-drawer. Otherwise no-op. |

---

## B. In scope — two founder-approved backend additions (THR-061 seq85)

Additive, EM authority (no schema / auth / permission changes). TDD red-first; OpenAPI snapshot regenerated in-PR (MEM-094) + web openapi-coverage (MEM-054).

1. **#314 Executor-prereq readiness** — additive read route `GET /api/v1/health/prereqs` → per-executor `{tool, present, path, hint}`, plus FE onboarding prereq messaging. **Test-safe:** must use an injectable/mockable presence check — a real `shutil.which()` in the executor-resolver path breaks executor tests on CI (local-green/CI-red, MEM-110/MEM-111).
2. **Task-Revisit web write (G3)** — expose the **existing** revisit mechanism (`happyranch revisit` / run_step revisit path) via a guarded task-action route + FE Revisit affordance with confirmation UX (pattern like resolve-escalation). **Guardrail:** if it would require a NEW `TaskStatus` value or transition → STOP + ESCALATE (founder-gated, MEM-044). GitNexus impact on the Python route; route `detect_changes` verbatim to the checker (MEM-067).

---

## C. Out of scope — parked for a separate backlog

All other §6 gated items (schema / auth / permission-model surfaces) are **explicitly excluded** from this branch and parked as a separate founder-sequenced backlog:

- **G5 / fan-out approval band** — ⛔ resurrects the deliberately-**deleted** fan-out review gate (THR-012 msg129/131, PR #290, MEM-108). **Do NOT build.**
- **G7 / Agents editors + Tools editor** — ⛔ permission-model / agent-config, founder-gated.
- **G9 / Settings writes + model-enum + detected-executor listing** — ⛔ auth/registration/permission flows; restyle only.
- G1 (#194) home rich-action write-paths, G2 (#202) threads enriched rows, G4 tasks subtask counts/filter/create-root, G6 (#204/#206) jobs metadata schema, G8 (#200/#201) KB candidate + facet routes, G10 (#217) work-hours routine write editor, G11 artifact-detail route + provenance schema, G12 (#312) onboarding template-list/Retry routes, G13 (#199/#93) Usage daily-series route, G14 dreams detail fields.
- **Retired / excluded surfaces:** `a-schedule` (THR-035-retired standalone Schedule page — content lives on Work Hours Wakes), `a-artifact-detail` (no route; highest honesty-fence risk), `a-audit` (excluded from the design set; already shipped keyset scroll #317/#318).

---

## D. Founder rulings baked into every slice (THR-061 seq79)

- **Thread-tasks rail = status-pill + id** (seq79) — overrides the artifact's id-only note.
- **Spend → Usage** rename (slice 6); keep `/spend` redirect.
- **Settings drops the Usage sub-tab** (Usage lives on the standalone page).
- **Threads drops the 320px master-detail split** (full-width list + separate detail).
- **DROP** the fan-out approval band, the standalone Schedule page, and the artifact-detail surface.
- **Honesty fence (strip everywhere):** no invented metrics/badges/roles/$, no per-child tokens/locale/progress, no "88% accept rate"/active-idle status, no Baloo 2 font, no raw hex.

---

## E. Merge discipline

- Each slice PR merges into `feat/thr061-ui-redesign` on APPROVE + PASS + CI 4/4 + CLEAN (EM authority).
- The umbrella PR (`feat → main`) stays **DRAFT** while slices land.
- When ALL slices land, the final `feat → main` merge is a **breadth change** — surfaced for founder sign-off, **not auto-merged**.
