# Health reliability — Claude Design handoff v1.1

| Field | Value |
| --- | --- |
| Status | draft, synchronized to PRD v1.1; final Founder sign-off/build approval pending |
| Owner | Product Lead (`product_lead`) |
| Date / task | 2026-11-28 / TASK-19790, continuing TASK-19671 |
| Contract | [Health reliability PRD](2026-11-28-health-metrics-reliability-prd.md), draft PR1924; [decision/sign-off packet](2026-11-28-health-reliability-signoff.md) |
| Authority | Founder THR-408 seq28/32; Product recommendations seq33; Engineering feasibility TASK-19748 and support/caveats seq35 |
| Boundary | documentation/design instructions only; no build, schema/data change, maintenance, restart, deploy, merge, successor or timeline approval |

## Instruction to Claude Design

Revise the Health investigation design against this handoff and the linked PRD. This v1.1 is the complete current handoff, superseding conflicting behavior in v1.0 (`att-617`, THR-408 seq19), the supplied v1.0 ZIP/spec and its pending-decisions note (seq20), and earlier conditional-rate advice. Preserve those artifacts as historical sources. The ZIP itself and its external canvas were not edited in TASK-19790; a corrected rendered prototype remains validation work. Do not import old rate behavior into production from that ZIP.

The design should help the Founder answer: is work executing, waiting or failing; what was observed and what is unknown; and where to investigate. Keep investigation primary. A connected page, fresh collector and successful workload are three independent facts. No universal health score or automatic recovery action.

## Settled choices and pending recommendations

| Item | Current instruction | Authority |
| --- | --- | --- |
| D1 | Required P0+P1 counts-first release; distinguish failures, waits and unreliable evidence | Founder seq28 |
| D2 | Forward-only attempt record with identity, start/outcome reconciliation and unresolved attempts; explicit recording/source coverage | Founder seq32, choosing Engineering seq31 over Product seq30 failure-only advice |
| Rate / design5 | Remove all launch-failure percentage widgets, rate-unavailable/zero-cohort placeholders, complete-data toggles and automatic rate unlocks. Retire M05 from rendered content. No rate returns merely because evidence becomes complete | Founder seq28; Product reconciliation seq33 |
| Pagination | At most 50 evidence summaries/page with cursor and “more results available”; no “of N” exact list total | Founder seq28; Engineering item11 |
| D3 | Use PRD numbers as proposed design targets, with annotations identifying pending Founder approval | Product seq33 / Engineering support seq35; not Founder-approved |
| Design6 | Late periodic loops as a separate supporting issue with age, threshold and diagnostic details; never a launch-failure count or inferred cause | Product recommendation seq33, Engineering support seq35; Founder pending |
| Design7 | Remove HTTP p95 history chart; retain current retained-sample table and honest sample labels | Product recommendation seq33, Engineering support seq35; Founder pending |
| Design8 | Side-by-side desktop investigation/work-now panels, investigation first in reading order; one column on phone | Product recommendation seq33, Engineering support seq35; Founder pending |
| D4 | Separate reserved stopped-daemon maintenance; Product recommends deferral | No maintenance authorized |

Production has no scenario selector, annotation toggle or synthetic-data control. Synthetic examples may live in an isolated design review venue, clearly labelled. They must never simulate eligibility to unlock a rate. All percentage widgets are omitted from this release; show sample coverage as observed/expected slot counts and missing intervals. The approximate storage-growth reduction remains a hypothesis in documentation, never a dashboard tile.

## Information hierarchy and interactions

1. **Context:** “Daemon · all loaded organizations”; 1h/24h/7d rolling window; timezone; browser transport status/time; independent live source time/age, persistence time/age and event coverage. Show partial/unknown/stale warnings before charts. Changing org URLs cannot relabel global counts.
2. **Investigation:** separate known failed launches, failures after starting, pre-start refusals, unresolved/unknown-stage attempts and cleanup obstruction. Counts state their event window, source scope and coverage. Known incidents remain visible when another source fails. Expose stage/time/reason and optional receipt, never invent a cause.
3. **Work now:** observed active leases/effective cap, admission queue/oldest original wait/head-only reason; exclusive task states; retained job status versus positive live observation and unknowns. Current values stay distinct from history cutoff. Preserve wait reasons and original timestamps on narrow screens.
4. **Trends:** actual time axis over requested [T−W,T), sampled peaks with time, deduplicated event counts, missing slots/gaps, explicit versus inferred restarts and bounded marker overflow. No line through a gap/restart. A gauge cannot establish absence of a short incident.
5. **Supporting diagnostics:** late loops, last completed run-step duration, backend probe age/capability, recent process-local receipt breakdown, HTTP retained-sample table. Resource details stay on canonical diagnostic sources.

Use tiles as filters if selection is explicit and keyboard reachable. Evidence details carry scope, attempt ID, producer, occurrence/start/outcome times, stage confidence, source coverage and canonical links. Back/close restores window/filter/page/inspector/scroll/focus. Loading does not show zero. Expose field meanings in details without requiring metric IDs in the user flow.

## Attempt and count truthfulness

The selected D2 records actual attempts before backend preparation, then positive start or known pre-start failure. Stable attempt identity and deduplication preserve retries without treating callback replays as launches. Pre-seam refusals are separate. Positive start followed by failure remains a post-start failure; no-outcome attempts remain unresolved. Keep primary outcome and cleanup result separate.

Display **known failures within stated coverage**, with source window/watermark and forward-only recording start. Attempt recording improves reconciliation but cannot prove that the initial write or every producer was captured. Missing first-write/producer coverage and recording failures must remain visible independently of recorded unresolved attempts. No historical backfill, perfect coverage or universal success claim. Exact metric counts must reconcile at a pinned cutoff; evidence page length is not a metric total. The existing default-LIMIT-50 job count defect must not be retained as a valid exact count.

## Source navigation and real capabilities

Engineering TASK-19748 source inspection (not click validation) confirms:

- Task `/orgs/:slug/tasks/:task_id`, job `/orgs/:slug/jobs/:job_id`, thread `/orgs/:slug/threads/:thread_id`, workflow `/orgs/:slug/workflows/runs/:runId`, and audit `/orgs/:slug/audit` pages exist. Owner detail needs a real retained/linked owner record and its actual org. Attempt-only evidence uses the audit list filtered by actual scope key and action.
- Audit API supports `task_id`, `agent`, `action`, ISO `since`, `limit`, `cursor`, `include_thread_origin`. Web deep links support `task_id`, `agent`, `action`, `since=24h|7d|all`; `class` is client-only. No `until`, `source` or offset exists. Preserve incident times/IDs in details; do not claim an exact end-bounded filter or pass an ISO timestamp as the web window token.
- There is no receipt-detail or audit-detail route. Receipt summaries have no durable identity/time/org/task association. Do not fabricate URLs from deque positions.
- `happyranch preparation-recovery TASK --org ORG --json` is a durable-data read-only task-only fallback. It may expose existing preparation/refusal evidence, but backend prepare/spawn failures can lack boundary rows and return null identity/cause with missing predicates. It does not cover threads, dreams, wakes, schedules or jobs. Keep it in task-refusal details, outside the required browser two-action path. Do not offer the human-only reconcile write.
- Audit keyset pagination exists; task paging exists; current jobs listing lacks a cursor and needs Engineering work. No exact evidence-list total is promised. “More results available” still needs real bounded cursor behavior; a stale or failed next page must not imply completeness.

Denied, missing, expired and unattributed destinations keep the known count/time and state the limitation. No cross-org URL substitution, private diagnostic bodies on public `/health`, or recovery/stop/retry/maintenance buttons. All four controlled journeys must reach usable evidence in at most two navigation actions; source route existence is not proof that those links work.

## Freshness, liveness and bounded history

The following D3 targets are **recommended, not yet Founder-approved**: current reads 15s / stale source age >45s; history reads 60s / stale collection age >180s; immediate refresh on return/window change. Equality remains fresh. Render actual age below thresholds too. Browser success cannot renew any source timestamp.

History uses proposed 1-minute/5-minute/30-minute buckets for 1h/24h/7d, ≤400 buckets per series. Preserve first/min/max/last with times, observed/expected slots, missing/invalid slots, gaps and restart uncertainty. Never connect across >180s collection gap or restart. Missing old fields stay unknown. Proposed marker cap is 200 per kind with explicit overflow; no silent truncation.

**Cached jobs:** Engineering proposes a bounded/cached liveness observer because the probe may take up to 1s. Show the actual cached observation time/age, method/platform, observed versus unobserved population and coverage separately from browser freshness. An old live observation cannot stay current-live merely because `/metrics` refreshed. Age/invalidation must lead to stale/unknown under the validated source policy. Cache cadence, maximum trustworthy age, limits, invalidation/restart and failed/unsupported behavior are Engineering-owned validation dependencies, currently unresolved. The 15s read and 45s proposal do not invent those facts. Retained `running`, missing PID or old age alone establishes neither live nor dead.

**Legacy history:** oversized old rows remain in the representative 30-day workload and the proposed history query p95 ≤500ms target. No automatic first-week waiver. A proposed rollout exception needs measurements, affected population, impact, mitigation, bounds/exit criteria and separate Founder acceptance. Design must show a real partial/stale/error outcome when queries fail; it cannot convert a performance miss into a pass or rewrite history.

Other proposed costs: history ≤1 MiB and current dashboard ≤128 KiB before compression; future aggregate snapshot ≤16 KiB at approximately one/minute; collection p95 ≤100ms. Preserve diagnostic records/access/retention separately. These are release test targets, not production SLAs.

## State catalogue to preserve from S01–S16

The table is a revision instruction and acceptance mapping, not a demonstrated checklist. Every row is **NOT RUN** in this task.

| State | Required representation / correction | Acceptance |
| --- | --- | --- |
| S01 Overview | Independent clocks, source-linked known counts, work now, trends/diagnostics; no rate line/control | AC-H08/H11/H14 |
| S02 Failed launch | Pre-start stage/time, no receipt allowed, real audit or owner path; refusal separate | AC-H02 |
| S03 Failure after start | Positive start, primary failure/timeout, cleanup separate; failed-launch count unchanged | AC-H03 |
| S04 Waiting | Capacity head wait, original oldest age, human/dependency/park/internal states distinct | AC-H04/H09 |
| S05 Collector stopped/resumed | Fresh transport with old persist time; retained caveated values and preserved gap after resume | AC-H05 |
| S06 History coverage | Full 24h/7d axis, peak/time, 2h and 45h gaps, explicit/inferred restarts; slot counts, no percentage | AC-H01/H06 |
| S07 Source unavailable | Retain last known timestamp and known incidents; no invented zero/verdict | AC-H11 |
| S08 Loading/refresh/window race | Pending selection labelled; later old response cannot overwrite new selection; focus stable | AC-H07 |
| S09 Empty / covered zero / unresolved | Distinct no stored observations, no known failures within coverage, unresolved attempts; remove zero-cohort rate state | AC-H08/H11 |
| S10 Unsupported/invalid/partial | Unknown cap/backend/producer coverage; invalid slots and unknown tasks visible | AC-H09/H11 |
| S11 Destination limits | Denied/missing/expired/Health-read-denied distinct; known count/time retained | AC-H11/H14 |
| S12 Job/task uncertainty | Fresh live versus aged cached live, unknown/unobserved jobs, conflicting task facts | AC-H09 |
| S13 Inspection | Keyboard/touch peak/gap/restart inspector, source definition and coverage details | AC-H01/H06/H14 |
| S14 Diagnostics | Periodic late/never/disabled distinct; interval-zero run-step no alarm; HTTP ring and ≤64 receipts own windows | AC-H10 |
| S15 Multi-org return | Global context; actual owner org destination; back restores original state | AC-H14 |
| S16 Bounded evidence | 50/page with more-results cursor; no exact-total variant; explicit marker/read exhaustion | AC-H11/H13 |

Use synthetic examples only as examples: e.g. four known failed attempts across three tasks, three post-start failures and two unresolved attempts. Do not reuse the old prototype's S/F ratio or rate query parameter. A sampled peak of nine at a stated time remains an observed peak. A coverage example can show 1,230/1,440 slots and 210 missing; it must not imply uptime.

## Tokens, layout, copy and accessibility

TASK-19748 item12 found all 44 Direction-A palette values, radii and shadows matching production at `30849be` and `780c6e0a6`; token names need translation. This is source/script evidence only. Prototype font sizes include off-scale 9.5/10.5/12.5/17/19/21px and Tasks-local 11.5px. Snap to existing production type tokens; any new token needs its own decision. Use production self-hosted fonts and CJK fallbacks. Core tokens did not change through fetched main `34494bd9cfa7551fb7b3d331f272add661cbf56f`; contrast and rendered parity remain NOT RUN.

The existing index-only Sparkline does not satisfy the time-axis contract. Time charts, event lanes, keyboard inspector and coverage details are new components; reuse existing Drawer and production primitives. No dependency/library approval follows from this design packet.

Recommended layout (design8): investigation before work now in reading order; side by side at desktop, one column on phone. At 390×844 and 1440×900, both themes and English/Simplified Chinese must preserve times, coverage, reasons, long IDs and reachable controls. Local table scrolling must be labelled/focusable; no page-wide overflow. New app copy ships in both locales. Suggested key copy:

| English | 简体中文 |
| --- | --- |
| Known failed launches · partial source coverage | 已知启动失败 · 来源覆盖不完整 |
| Unresolved attempts | 结果未明确的启动尝试 |
| Job liveness unknown | 作业存活状态未知 |
| Last observed {time} · {age} ago | 上次观测于 {time} · 距今 {age} |
| More results available | 还有更多结果 |

State meaning uses text/shape as well as color. Charts have text summaries and keyboard/touch access to the same data. Drawers contain focus, close via Escape, and return focus to their trigger. Refresh/live announcements do not steal focus or announce every number. Reduced motion preserves meaning. Locale changes preserve focus/selection/draft and cause no extra request/mutation. Written requirements are not accessibility evidence.

## UX01–UX14 handoff checks

| Check | Required proof / revision | Status |
| --- | --- | --- |
| UX01 | Global scope, actual org links and current/history distinction (AC-H14) | NOT RUN |
| UX02 | Connection/collector/backend/workload can disagree truthfully (AC-H05/H11) | NOT RUN |
| UX03 | Failed launch without receipt, refusal separate, real evidence within two actions (AC-H02) | NOT RUN |
| UX04 | Positive start then failure, cleanup separate (AC-H03) | NOT RUN |
| UX05 | Capacity, wait/task states and exact reconciled counts, >50 jobs (AC-H04/H09) | NOT RUN |
| UX06 | Full rolling axis, peaks/gaps/restarts and an actual DST-crossing fixture (AC-H01/H06) | NOT RUN |
| UX07 | Stopped/resumed collector keeps actual age/gap (AC-H05) | NOT RUN |
| UX08 | Empty/covered zero/unresolved/denied/unsupported/partial/stale/unavailable distinct (AC-H08/H11) | NOT RUN |
| UX09 | No percentage/placeholder/production fixture control or automatic unlock in any state (AC-H08) | NOT RUN |
| UX10 | Advancing windows, response races and real return-to-tab refresh (AC-H07) | NOT RUN |
| UX11 | Honest HTTP/receipt/run-step labels; separate late-loop issue (AC-H10) | NOT RUN |
| UX12 | Screen reader, keyboard, focus, contrast, both themes/locales/viewports and rendered layout (AC-H11/H14) | NOT RUN |
| UX13 | Cursor/more-results/exhaustion; oversized legacy performance included (AC-H13) | NOT RUN |
| UX14 | Source fields/coverage/unknowns and cached liveness observation age independent of API freshness (AC-H09/H12) | NOT RUN |

The old prototype's claimed 11/14 demonstrated checks are historical author claims. TASK-19790 inspected ZIP/spec source and decisions only; it did not render or run the design, accessibility/contrast, DST, working links, storage/performance or runtime acceptance. No completed validation is inferred from the checklist or source parity. The four distinct mandatory scenarios remain failed launch, post-start failure, capacity wait and stopped collector. This handoff closes the bounded documentation task; implementation and further validation need the pending Founder build decision and normal Engineering process.
