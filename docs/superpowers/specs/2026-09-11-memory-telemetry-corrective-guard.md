# Memory telemetry corrective guard

> Status: current
> Current Source: `docs/agent-guides/features-and-invariants.md` and `docs/agent-guides/web-and-cli.md`
> Notes: TASK-7767 is a guard-only serial merge unit; collection and tuning remain unshipped.

> Guard repair note (TASK-7781): current/invalid and malformed diagnostic input
> must fail closed before arithmetic. Counts remain explicitly observation-only,
> threshold readiness is false, and canary-gated collection has not started.
> Tuning-decision branch tests remain deferred to the independently reviewed
> versioned implementation.

> Narrowed guard boundary (TASK-7832): the backend and CLI retain separate
> report-local validators. Observation-only malformed read/search diagnostic
> parity and the remaining controlled-clock whole-report finite matrix are
> versioned-reporting acceptance obligations, not assertions passed or closed
> by this guard-only unit.

## Frozen follow-on definitions (not yet shipped)

The intended population is runtime task sessions, including root and child
tasks; threads and dreams are excluded. Health evidence must independently
originate through the actual child → canonical CLI → validated route path and
be compared with intended task-session launches, so missing transport is
detectable even with zero reads. A concrete health threshold and probe design
require independent review with transport before collection.

TASK-7864 supplies only the bounded transport prerequisite: executor children
forward their actual invocation session in a private environment hint consumed
by canonical memory get/search when no explicit `--session-id` is supplied.
TASK-9561 recovers that published ancestry and the preserved F1/F2 checkpoint on
a replacement branch under founder THR-091 seq220/219. Environment assembly with
no runtime SID passes an explicit empty hint to defeat final overlays. Ordinary
and custom execution forward their resolved invocation SID, including generated
IDs, which remain uncredited unless registered. Nonempty explicit CLI flags win;
empty flags preserve environment fallback. The hint never substitutes a provider
resume ID or server-side route validation. Immediate shipping cases cover
Claude/Codex root/child bootstrap, every read/search row tuple and scope, actual
resume argv, and deterministic same-agent operation/end isolation. Codebuddy,
additional contained/custom variants, forged-task nonoverride, thread/dream
population and genuinely nonshown search follow-on remain OPEN manager-owned
canary obligations. Collection, reporting, thresholds and tuning remain unshipped.

A clean epoch begins only after deployed production-canary acceptance. Its
earliest qualifying timestamp is the deterministic minimum, never input order.
Observation requires 14 complete deployed days and at least 500 valid nonempty
task-digest sessions, with per-agent eligibility of at least 30 and separate
manager/worker descriptive breakdown.

Pointer pull-through is unique exact epoch/org/agent/task/session/memory
shown-pointer pairs subsequently read in that same session divided by unique
shown-pointer pairs. Full-body directives are excluded from pointer
opportunities (budget-fallback pointers count); full-body-only digests count as
nonempty session volume but have zero pointer opportunities. Agents with no
pointer opportunities cannot vote in the activation majority. Activation is a
candidate only when aggregate pull-through is below 10% and a strict majority
of eligible functional agents are below 10%. Otherwise, a nonshown
search-sourced read fraction above 25% evaluates aliases first: numerator is
nonshown search-result read pairs and denominator all valid search-sourced read
pairs; full-body exposure is shown. Deduplicate exact pairs and exclude
synthetic, manual, and legacy rows. Insufficient health/sample/functional
evidence cannot select tuning. Per-(agent,memory) read counts are
observation-only; ranking remains unchanged during the window.

## Render-observed producer contract (TASK-9590 / TASK-9559)

Task bootstrap retains text and item IDs from one actual `MemoryStore.render_memory_digest` pass; `build_memory_digest` remains a compatible string/None wrapper. Only appended full blocks or pointer lines contribute IDs. Directive fit is full-body exposure; budget fallback is pointer exposure. IDs merely mentioned in bodies, titles, header, or nudge are not item exposure. Scoring, ordering, salience, character budgets, prompt bytes and nudge behavior remain unchanged. A header/nudge-only result has no items and emits no impression.

After trusted task/session binding and before launch/session_start, one existing `memory_digest_impression` stores accurate `digest_ids`/`digest_count` with exactly three new keys: `memory_telemetry_version=1`, `pointer_ids`, `full_body_ids`. Lists are unique and disjoint, with union equal to the digest IDs/count. If duplicate files render one ID in both forms, the observed full body owns that ID; no duplicate opportunity is recorded. No prompt, title, body, query or brief is logged. Optional metadata rejects inconsistent types/version/duplicates/overlap/union before insertion. Logger calls without metadata retain the byte-equivalent old unversioned payload; no history is inferred, upgraded or backfilled. Existing audit action and actual task row scope stay unchanged.

The unchanged source resolver checks accurate digest IDs before validated search results, so a body-mentioned but nonrendered `MEM-999` can receive search attribution after an actual search, while the shown item stays digest-sourced. Read/search writers, SessionTracker validation, task/session identity and eligibility are unchanged. Recovery/unattributed rows gain no task eligibility from a version field. This producer does not remove either report guard: backend and CLI stay fail-closed. G1 independent canary/epoch authority, G3 complete intended-launch/expectation census, G4 consistent acquisition, the whole-report matrix and installed health acceptance remain OPEN. `session_start` records intended invocation, not a complete process-launch census. No canary is accepted, clean epoch started or collection enabled by this metadata.

The producer source tests use disposable provider-boundary stand-ins for Claude/Codex ROOT/CHILD, exact R11b/c prompt sizes, and shown-get/search/genuinely-nonshown-get. They do not establish installed canary, codebuddy/custom/contained host coverage or G1/G3/G4 acceptance. Independent review, behavioral QA, CI and merge remain delivery gates.
