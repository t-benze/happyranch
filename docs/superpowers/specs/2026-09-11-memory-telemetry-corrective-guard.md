# Memory telemetry corrective guard

> Status: current
> Current Source: `docs/agent-guides/features-and-invariants.md` and `docs/agent-guides/web-and-cli.md`
> Notes: TASK-7767 is a guard-only serial merge unit; collection and tuning remain unshipped.

> Guard repair note (TASK-7781): current/invalid and malformed diagnostic input
> must fail closed before arithmetic. Counts remain explicitly observation-only,
> threshold readiness is false, and canary-gated collection has not started.
> Tuning-decision branch tests remain deferred to the independently reviewed
> versioned implementation.

> Historical narrowed guard boundary (TASK-7832): that guard-only checkpoint
> retained separate report-local validators. The reporting core below supersedes
> that separation; the historical failed parity receipts remain FAILED. Observation-only malformed read/search diagnostic
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
canary obligations. Collection, full eligibility reporting, thresholds and tuning remain unshipped.

The S04 ordinary source correction retires an invocation's existing SessionTracker
binding at the final `executor.run` return/exception, after any internal 429
attempts. The unchanged generation-safe clear preserves newer same-task sessions
and other same-agent tasks. Real blocked-job resumption uses a new runtime SID;
a genuine failed-child retry retains the failed child's parent, results and history
and uses the required direct revisit link. Retired/provider/generated hints remain
usable for get/search without task/session credit while the successor is active.
This ordinary source proof does not establish installed, custom or contained
acceptance, collection health, a clean epoch or whole-report completion.

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

Task bootstrap retains text and item IDs from one actual `MemoryStore.render_memory_digest` pass; `build_memory_digest` remains a compatible string/None wrapper. Only appended full blocks or pointer lines contribute IDs. Directive fit is full-body exposure; budget fallback is pointer exposure. IDs merely mentioned in bodies, titles, header, or nudge are not item exposure. Scoring, ordering, salience, character budgets, prompt bytes and nudge behavior remain unchanged. A header/nudge-only result has no items and emits no impression. Only appended items with string IDs satisfying the existing `ID_RE.fullmatch` contribute identity metadata. Null, missing, nonstring or malformed IDs remain rendered byte-for-byte under the existing permissive parser, but contribute no identity or ID fragments from their representation, title or body. A malformed-only digest still launches normally and emits no impression; valid neighbors retain exact modes/counts. Strict writer validation still rejects malformed caller-supplied metadata before insertion.

After trusted task/session binding and before launch/session_start, one existing `memory_digest_impression` stores accurate `digest_ids`/`digest_count` with exactly three new keys: `memory_telemetry_version=1`, `pointer_ids`, `full_body_ids`. Lists are unique and disjoint, with union equal to the digest IDs/count. If duplicate files render one ID in both forms, the observed full body owns that ID; no duplicate opportunity is recorded. No prompt, title, body, query or brief is logged. Optional metadata rejects inconsistent types/version/duplicates/overlap/union before insertion. Logger calls without metadata retain the byte-equivalent old unversioned payload; no history is inferred, upgraded or backfilled. Existing audit action and actual task row scope stay unchanged.

The unchanged source resolver checks accurate digest IDs before validated search results, so a body-mentioned but nonrendered `MEM-999` can receive search attribution after an actual search, while the shown item stays digest-sourced. Read/search writers, SessionTracker validation, task/session identity and eligibility are unchanged. Recovery/unattributed rows gain no task eligibility from a version field. This producer does not remove either report guard: backend and CLI stay fail-closed. G1 independent canary/epoch authority, current-serving G3 census acquisition/acceptance, The observation-only reporting core below supplies read-side G4 acquisition; full eligibility reporting, current-serving census acceptance and installed health remain OPEN. `session_start` records intended invocation, not a complete process-launch census. No canary is accepted, clean epoch started or collection enabled by this metadata.

The producer source tests use disposable provider-boundary stand-ins for Claude/Codex ROOT/CHILD, exact R11b/c prompt sizes, and shown-get/search/genuinely-nonshown-get. They do not establish installed canary, codebuddy/custom/contained host coverage or G1/G3/G4 acceptance. Independent review, behavioral QA, CI and merge remain delivery gates.

## Observation-only G3 source census (TASK-9630 / TASK-9559)

The source-side G3 observer is attached by real `OrgState` construction before
workers. One actual `_run_agent` entry reserves an independent boot-local ordinal
before telemetry writes. Metadata-only `memory_runtime_intent`,
`memory_runtime_identity`, `memory_runtime_expectation`, `memory_runtime_binding`,
`memory_runtime_launched`, `memory_runtime_terminal` and `memory_collection_seal`
use the existing actual-task audit scope. SID is observed at its original
assignment; parent relation and recovery purpose come from runtime facts,
never provider resume or request claims. Provider retries add started-callback
occurrences under one logical invocation. Each terminal belongs to its own
ordinal/runtime SID; no latest-agent session-end inference is used.

Expectation freezes the same structured render/config used by the unchanged
prompt: `disabled/budget_zero`, `empty/memory_directory_absent`,
`empty/renderer_empty`, `empty/no_valid_rendered_ids`, or
`nonempty/rendered_ids`. Text presence is separate from eligible item exposure;
full-body-only exposure is nonempty with zero pointer opportunities. No content
is recorded and no missing expectation is inferred from an absent impression.

Live attempted/persisted counts and chained digests are independent of stored
rows. A short metadata lock never spans rendering, execution or application
callbacks. Observation-boundary persistence drains concurrent metadata in
generation order; another invocation's callback need not wait on a blocked
observer writer. Writer failures are sticky and affect observation availability
only. Seals checkpoint independent counters/digests without reading history;
their diagnostic integrity is `census_not_reconciled`, never a validation or
health claim. Exhaustive zero-write read validation rejects missing/duplicate/
corrupt phases and seals, missing starts or expected impressions at zero reads,
unresolved population/parent/type facts, unknown/pending preparation and zero
population. It compares opening/closing semantic observer facts (including
errors, pending writer/preparation state and seal progress), excluding sample
timestamps. Audit history is acquired in primary-key pages of at most 256 rows,
with no shared Database lock across pages or decoding, and a 100,000-row total
work limit including unrelated/prior-boot history. Same-connection write counts
and other-connection data versions bracket acquisition. Movement, read failure
or work exhaustion returns unavailable; no partial history is validated and no
retry-until-quiet loop or reader write is performed. A restart allocates a new
observer boot and never reconstructs old completeness. Constructor/attachment
failure preserves ordinary org startup with explicit unavailable observation.

This census and its seals are not collection health, installed acceptance or
epoch authority. G1, current-serving acquisition/B1, both health consumers,
installed canary/deferred executor/population coverage and full reporting remain
unimplemented here. Existing backend and canonical CLI guards remain
`insufficient_instrumentation` with `thresholds_met=false`; no collection/epoch
transition, deployment, tuning or 48-hour clock starts from this source unit.

## Observation-only reporting core (TASK-9673 recovery / TASK-9559)

The backend and canonical CLI share the pure `memory_telemetry_report` reducer.
They acquire `session_start`, impressions, reads and searches before any empty or
short return. Backend acquisition uses one synchronized SELECT statement snapshot;
CLI exhausts real `/audit` pages at limit5000 in two complete sweeps and compares
relevant audit identities/content and `/agents` roles at one aware UTC cutoff.
Events at or after that cutoff are excluded. Backdated/content/role drift,
cursor/schema errors, HTTP/decoder/timeout or SELECT failures refuse with
`acquisition_unavailable`; CLI writes only that category to stderr and exits1,
with no partial JSON. Unavailable `/agents` stays explicitly unknown and allows
safe descriptive counts; it never supplies functional cohort authority.

Structural returned-data corruption produces the full empty-metrics error object
(null first event, days0, empty aggregate/by_agent/by_role/read_counts, explicit
errors). Task-only sources are `digest`, `search`, `explicit_or_other`; unsupported
strings use `invalid_source`, nonstrings `malformed_read_source_type`, and causal
or digest-first contradictions `source_contradiction`. Independently identified
manual/thread/dream/recovery/legacy populations are excluded before task source
validation. Runtime task credit requires the actual task/agent/runtime-session
start tuple; prefixes, client claims and SID-only matching cannot establish it.

Version1 observed pointer/full-body lists are validated, disjoint and duplicate
free, with union matching digest IDs/count. Legacy impressions remain unversioned
with unavailable mode metrics; no memory-file/body/directive inference occurs.
Exact tuple+memory pairs deduplicate opportunities and reads; secondary activated
sessions and per-agent/role/memory operation counts remain descriptive. Search
ratios use distinct persisted, causally corroborated search-sourced read pairs.
Earliest qualifying impression is a deterministic aware-UTC minimum. Elapsed
complete UTC days exclude partial first/current days. Raw day/session sample
flags may be true; thresholds, diagnostics_valid_for_collection and
evaluation_candidate remain false, decision `insufficient_instrumentation`.
`session_start` records audited intended invocations, never a complete process
launch/expectation census. G3 source observation is shipped separately; the report does not acquire its
current-serving census authority. Trusted canary/epoch, census and probe
health remain UNKNOWN/unavailable in the report. There is no collection/tuning, ranking write,
synthetic/natural eligibility claim, authority override or epoch start.
The read-side snapshot/two-sweep contract detects observed drift; it adds no
writer fence or hostile same-UID guarantee. Full eligibility, operational H-v1
and actual shipping/installed canary cases remain separately gated.
