# Health reliability — decision ledger and single sign-off packet

2026-11-28 · Product Lead · TASK-19790 · **draft / analysis-only**

Review together: [PRD v1.1](2026-11-28-health-metrics-reliability-prd.md) and [Claude Design handoff v1.1](2026-11-28-health-metrics-claude-design-handoff.md), in existing draft PR1924. This packet updates the v1.0 pending-decisions attachment at THR-408 seq20. It records choices and seeks one final Founder sign-off; it records no new approval.

## Decision ledger

| Item | Evidence and disposition |
| --- | --- |
| PRD first | Founder seq13; original TASK-19671, PR1924 at `58a9ef3125f937e5bd1348a0953f7c8ee809f074`, private PRD `att-616` |
| D1 required release | **Settled, Founder seq28:** counts-first required P0+P1 cutline |
| Launch-failure percentage / design5 | **Settled, Founder seq28**, retained through seq32/33: omitted entirely; no widget, rate placeholder, fixture control or automatic unlock. A future return needs separate Founder approval |
| D2 attempt record | **Settled product choice, Founder seq32:** attempt record over failure-only. Stable identity, positive start/outcome reconciliation, unresolved attempts, known-failure counts and source coverage. Forward-only, no historical backfill or perfect-coverage guarantee |
| Evidence pagination | **Settled, Founder seq28:** more-results-available variant, no exact list total. Exact metric counts still reconcile to their source population |
| D3 numeric budgets | **Founder pending:** Product seq33 recommendations, Engineering support seq35. Measured gaps cannot silently change acceptance |
| Design6–8 | **Founder pending:** separate late-loop supporting issue; remove HTTP p95 history chart/keep sample table; side-by-side desktop panels, investigation first, phone single column. Product seq33 / Engineering seq35 |
| D4 | **Separately reserved:** Product recommends deferral. No disk reclamation/daemon stop is included or required for release |
| Final PRD/build | **Founder pending:** Engineering seq35 proposes combined approval after this delivery |

Product seq30 failure-only recommendation and seq16/23/25/26 conditional-rate language are superseded. Engineering feasibility proves source capabilities, not implementation, runtime coverage or product authorization.

## Recommended D3 targets for Founder decision

| Area | Proposed target |
| --- | --- |
| Current | Read every 15s while visible; source stale at age >45s; immediate return-to-tab refresh |
| History | Read every 60s; persisted collection stale at age >180s; immediate return/window refresh |
| Charts | ≤400 buckets/series; preserve observed peaks, missing intervals and explicit/inferred restarts |
| Payload | History ≤1 MiB, current ≤128 KiB, both uncompressed; evidence ≤50 summaries/page |
| Future snapshots | Aggregate payload ≤16 KiB at approximately one/minute; diagnostic records remain separate |
| Cost | Collection p95 ≤100ms; history query p95 ≤500ms over 100 warm calls on the documented 30-day representative workload including oversized legacy rows; cold timing separately reported |

Keep the PRD duration-aware periodic-loop threshold `age > max(3×interval, 2×last_duration + interval, 45s)` for interval >0. Last run-step duration is not periodic-loop lateness. These are recommended measurable release targets, not SLAs, current performance claims or external timelines. Approximately 75% less future dashboard growth remains a comparable-workload hypothesis.

**Two required qualifications, without relaxing acceptance:**

- **Legacy rows:** the old oversized history remains in the representative legacy test and ≤500ms p95 target. A measured miss is a miss. There is no automatic first-week waiver. A temporary rollout exception needs measured evidence, affected population/queries, impact, mitigation, bounds/exit criteria and separate Founder acceptance.
- **Cached job liveness:** Engineering's caching approach must expose actual observation time/age, method and coverage independently of browser freshness. A refreshed API cannot keep an old observation current-live. Unknown remains unknown. Engineering must fix and validate cache cadence, trustworthy-age threshold, limits/platform coverage, invalidation/restart and failure behavior; those details are unresolved dependencies, not invented targets.

## Proposed combined build approval — not yet granted

If the Founder approves, the proposed goal is to implement the signed-off Health reliability contract. Done means AC-H01–H14 pass with source reconciliation and the stated limitations, including **all four distinct scenarios**: failed launch, post-start failure, capacity wait and stopped collector, each with a usable investigation path. A statement listing only three cases does not replace AC-H03.

Engineering seq35 proposes **approximately 6 PRs**:

1. Approximately 3 PRs of projections over existing data: freshness/coverage, exclusive states, bounded history, restart/loop/probe and liveness views.
2. One PR for forward-only launch attempt instrumentation, with full launch-path review.
3. UI work against the PRD/handoff, with bilingual and accessibility evidence.
4. The jobs-count default-LIMIT-50 correction, with exact population reconciliation.

This grouping is an estimate, not a fixed implementation allocation; TASK-19748 separately estimated 3–4 web PRs for new components. Engineering must reconcile the final breakdown before execution within whatever scope/budget the Founder grants. No automatic extra PR, attempt or budget increase follows from the discrepancy.

The proposed allowance is **≤3 fix rounds per PR**, with normal independent review, scoped QA, required CI and guarded merge. Neither that allowance nor implementation is granted in this document. No successor/implementation dispatch is made by TASK-19790. No external delivery date is proposed.

Keep separately reserved: D4 maintenance, schema migrations/new storage/indexes, changes to access/retention/admission, restoring a percentage and changes to D3 acceptance. Operational rollout/deployment/restart still requires its own authority. A final product/build sign-off is not a merge, deploy or maintenance receipt.

## Feasibility and outstanding proof

TASK-19748 inspected source `780c6e0a61bb971836bdf1949d119c702513293a`; TASK-19790 fetched `34494bd9cfa7551fb7b3d331f272add661cbf56f` and checked intervening source differences in a guarded worktree. Relevant core sources/tokens remain unchanged; loaded daemon parity is **UNVERIFIED**. The edit continues the existing PR head without mixing unrelated main changes.

Actual task/job/thread/run/audit destinations exist in source. Attempt-only evidence uses the audit list's real scope/action filters; web time filters are `24h|7d|all`, API `since` is ISO. No `until`/`source` filter or receipt-detail page exists. Preparation recovery is read-only, **task-only**, and cannot generally diagnose backend prepare/spawn failures without boundary evidence. Audit/tasks can keyset-page; jobs need cursor work. No cheap exact evidence total is promised; no schema index is authorized. Production palette/radii/shadows match the source prototype, but font sizes need existing token alignment and production font/CJK handling. These are feasibility findings; **working links, contrast and rendered parity are NOT RUN**.

All release runtime acceptance, exact metric reconciliation, launch recording/coverage/failure-window tests, cached-liveness timing, legacy cost measurements, accessibility/screen reader/contrast, desktop/phone rendered layout, both locale/theme validation, DST-crossing fixture and return-to-tab behavior are **NOT RUN** in this documentation task. The old prototype's 11/14 claim is not accepted proof. Required validation remains with Engineering/QA after authorization.

## Founder response scope

One response may approve or amend **PRD v1.1, D3, design6–8 and the proposed build scope/fix allowance together**, while preserving settled seq28/32 choices and separately reserved operations. No exception is requested for legacy performance. Cache details remain Engineering-owned validation dependencies. Silence, Engineering concurrence, this document's publication and this task's completion are not approval. The bounded documentation task ends after concrete delivery.
