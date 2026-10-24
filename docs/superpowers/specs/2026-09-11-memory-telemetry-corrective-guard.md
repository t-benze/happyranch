# Memory telemetry corrective guard

> Test-policy amendment (THR-291 seq40): Python unit source and gates cited below
> are retired. Product invariants and acceptance obligations remain unchanged;
> replacement E2E coverage is PENDING. See [the coverage gap record](../../python-test-reset.md).

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

The unchanged source resolver checks accurate digest IDs before validated search results, so a body-mentioned but nonrendered `MEM-999` can receive search attribution after an actual search, while the shown item stays digest-sourced. Read/search writers, SessionTracker validation, task/session identity and eligibility are unchanged. Recovery/unattributed rows gain no task eligibility from a version field. This producer does not remove either report guard: backend and CLI stay fail-closed. G1 independent canary/epoch authority and current-serving G3 census acceptance remain OPEN. The observation-only reporting core below supplies read-side G4 acquisition; full eligibility reporting, current-serving census acceptance and installed health remain OPEN. `session_start` records intended invocation, not a complete process-launch census. No canary is accepted, clean epoch started or collection enabled by this metadata.

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
population, subject only to the closed failed-before-exposure class below.
It compares opening/closing semantic observer facts (including
errors, pending writer/preparation state and seal progress), excluding sample
timestamps. Audit, task and result history is acquired in primary-key pages
of at most 256 rows, with no shared Database lock across pages or decoding,
and a 100,000-row bound per table including unrelated/prior-boot history.
Same-connection write counts
and other-connection data versions bracket acquisition. Movement, read failure
or work exhaustion returns unavailable; no partial history is validated and no
retry-until-quiet loop or reader write is performed. A restart allocates a new
observer boot and never reconstructs old completeness. Constructor/attachment
failure preserves ordinary org startup with explicit unavailable observation.

Future phase persistence failures emit one best-effort bounded ordinary log with
phase, frozen queued org/boot/ordinal/task/runtime SID, exception class, and only
explicitly allowlisted SQLite integer code/name pairs (otherwise `unknown`).
The direct invalid-positive-INTEGER audit-reference check has its own category.
Malformed or oversized identifiers/class names use fixed fallbacks. No exception
text, traceback, SQL, payload, credentials or root paths are logged. Logger failure
cannot alter sticky failure, counters/digests, sealing or subsequent draining.
These diagnostics cannot recover or establish the cause of an old lost exception;
source delivery does not establish installed health or collection acceptance.

Founder THR-091 seq428 permits one closed failed-before-exposure class. Exactly
one ordered authenticated intent -> identity -> raised terminal must agree on
org/org-root/boot/ordinal/task/agent/runtime SID, actual parent/type/population
and supported failed-task lifecycle. The terminal requires Boolean false
`expectation_known` and `binding_known`, INTEGER zero `launched_callbacks`
(excluding Boolean/float/string zero), and explicitly null `success` and
`returncode`. Missing fields, unknown facts, duplicates or contradictions refuse.
Complete bounded audit/task/result acquisition must prove absence of expectation,
binding, session_start, impression, launch, result callback and credited
get/search for that exact tuple. No filtered stream or empty default supplies
absence proof. Independent attempted/persisted counts/digests, all ordinals and
full seal history remain mandatory, with stable semantic bookends and no error,
pending writer or preparation. Observer acquisition pages all three tables in
at most 256-row SELECTs with a 100,000-row bound per table and revision bookends.
The failed ordinal remains explicitly classified in internal census results and
all full counts/digests/seals; it creates no shown/natural-session denominator,
expectation, binding, start, callback or credit. All-failure/zero qualifying
populations remain insufficient; ordinary exposed and lawful in-flight checks
remain. Other retry/resume/SID evidence stays separate. This classification
supplies no B/C/health/epoch acceptance, temporal POST-retirement zero, source/
boot/cohort authority, threshold change, reset or history rewrite. The health
definition digest binds this closed seq428 clause; existing receipts and epoch
bytes remain immutable and cannot qualify a changed installed source.

Founder THR-091 seq532 (approving531/529, preserving514/519's other
safeguards) adds a separate closed POST-expectation no-AGENT-launch class.
Exactly one authenticated ordered intent -> identity -> expectation -> raised
terminal must retain the same org/org-root/boot/ordinal/task/agent/runtime SID,
original parent/type/population/purpose and complete failed lifecycle/ancestry.
The full expectation retains truthful renderer/version/digest/budget/ID facts;
the terminal requires Boolean true expectation_known, Boolean false
binding_known, exact INTEGER0 launched_callbacks and explicit null success and
returncode. Positive supported CURRENT-attempt native refusal evidence is
mandatory in addition to exhaustive absence of binding/start/impression/launch/
result/credited activity. The supported path is the source-resolved initial
host ordinal-zero retry bookkeeping cleanup_failed raise BEFORE FIFO admission,
backend preparation, commitment and provider launch. Exact native invocation,
attempt, logical-owner and reserved SID attribution/order must agree; duplicate,
malformed, contradictory or unknown paths refuse. All four traceback
frames must match one complete supported source path: current identity-bound
statement sites, or the separately pinned shipped5a19e562 historical path
(orchestrator1506/1817, host1402, Pause510). Historical Pause510 was the cleanup
guard; current Pause510 is a different coding guard. Mixed/unknown paths
refuse. Later host retries, partial launch and callback failure cannot use this initial-entry path.
An inherited first_cause, repository_pull, kind=refused, launch_observed=false,
zero steps or absent callbacks/rows alone never establish no AGENT launch.
Preparation Git processes are distinct from the AGENT provider.

Preparation/outer compensation, cleanup, close/release and native custody are
NOT eligibility predicates for this new class. Preserve their uncertainty
separately without calling it clean, settled, quiescent or closed. Unknown AGENT
launch still refuses. No new producer, custody witness/API, writer relocation,
false flag, duplicate terminal, historical backfill or owner clearing is supplied.
TASK11977 is evaluated by its actual tuple and supported path, with no task-ID
exception or automatic exclusion. Its cleanup/Git root cause remain UNKNOWN;
JOB4909 remains a failed acquisition before validators, never a census verdict.

Both classes retain all ordinals/attempted-persisted counts/digests/full seals,
sticky errors and complete bounded selected acquisition at stable bookends.
Closing and atomic rereads rediscover membership; failed, moving, pending,
truncated or exhausted reads refuse. A class never repairs invalid global
census evidence; all-failure populations remain insufficient. Only classified
invocations receive zero shown/natural denominator/credit, preserving other
sessions on the same task. Native AuditLogger and canonical HTTP CLI share the
same reduction. The changed health digest and loaded identity bind all trusted
reader helpers and native launch-order/refusal functions. Original receipts and
epoch bytes remain immutable; changed source requires genuine fresh qualified
acceptance. Source/static checks supply no B/C, installed health or epoch PASS.

This census and its seals are not collection health, installed acceptance or
epoch authority. The seal-action serving source view is described here. G1
durable acceptance/B1, both health consumers,
installed canary/deferred executor/population coverage and full reporting remain
unimplemented here. Existing backend and canonical CLI guards remain
`insufficient_instrumentation` with `thresholds_met=false`; no collection/epoch
transition, deployment, tuning or 48-hour clock starts from this source unit.

Capture task comparisons use one ordered pair relation (THR-091 seq557/559/560).
The unary projection excludes only valid rendering timestamps at
`pause.observed_at`, `pause.blockers[*].observed_at` and `child_wait.observed_at`.
An otherwise equal pair may additionally advance BOTH `last_heartbeat` and its
accompanying `updated_at`: all four stamps must be present aware strings and
both later instants must be strictly forward. Updated-at-only, heartbeat-only,
equal/reverse or missing/malformed/naive changed stamps refuse. Exact unchanged
projected pairs remain equal; malformed/unknown values never disappear or
justify a changed pair. All other fields, membership and JSON scalar types stay
significant. Raw tasks remain intact. Native/HTTP reports, repeated dependency
captures, acceptance preparation and current-epoch captures share this relation;
locked publication rereads and control invariants retain exact record equality.
Serving movement still requires one fresh complete stable pair under the shared
budgets below; an earlier failed pair never supplies an accepted half.

The existing task invocation-exception catch logs only task/agent/reserved runtime
SID (or explicit unavailable), exception class and at most eight bounded frame
locations, with truncation stated. External filenames are hidden; exception
messages, source lines, prompts, credentials and frame locals are not added to
this diagnostic. Existing failure notes, failure/terminal/parent-wake/thread
behavior and control-exception propagation remain unchanged. Diagnostic/logging
failure cannot replace those effects. This supplies future metadata, not a cause
or repair for the original unknown launch exception and not measurement acceptance.

## Current-serving source observation (TASK-9734 / TASK-9559)

Founder THR-091 seq268/269 releases the seq240 seal-only optional response
extension. `GET /api/v1/orgs/{slug}/audit?action=memory_collection_seal` retains
exact entries, cursor, filters, enrichment, cursor errors and token dependency,
and adds `memory_collection_observation` from the serving OrgState. Every other
action response is unchanged; there is no new query option or authority flag.
TS `AuditResponse` permits legacy absence/null and mirrors the closed object.

Outer keys exactly: `contract_version` (integer 1 excluding bool), `org`,
`boot_id`, `installed_identity`, `generation`, `assigned_intents`, `intent_digest`,
`phase_counts`, `phase_digests`, `active_preparations`, `observation_error`,
`latest_seal_audit_id`, `epoch_id`, `epoch_audit_id`, `sampled_at`, `data_through`.
Both epoch refs are always null in this unit. Missing/failed/busy observer fields
are null rather than invented healthy zeros. Real empty observers report known
N0 and remain unaccepted. Phase maps have exactly intent/identity/expectation/
binding/launched/terminal and attempted/persisted counts or SHA256 digests.
Active preparations retain exactly ordinal/task_id/agent/session_id/
expectation_known from the producer. Counts are nonnegative integers excluding
bool; SHA256 values are lowercase hex; timestamps are aware UTC. Category-only
errors disclose no exception contents. Sticky writer errors remain sticky;
GET acquisition failure never changes observer or application state.

`installed_identity` is null when acquisition fails; otherwise its exact keys
are `source_root`, `runtime_root`, `org_root`, `package_version`, `python`,
`loaded_code`, `files`, `teams_sha256`, `cohort`, `profiles`, `backend`:

- `python`: executable (canonical realpath), version, implementation, cache_tag.
- `loaded_code`: sorted records of module, qualname, origin, loaded_sha256,
  source_code_sha256. Interpreter-bound fingerprints include bytecode,
  constants/nested code, names/variables/freevars/cellvars, flags/arguments,
  filename/qualname and line/exception metadata. Declared functions are actual
  Orchestrator _run_agent/_run_agent_impl, _resolve_executor_name/
  _resolve_model_name/_build_executor, actual bound _launch_agent_with_scratch/
  _run_agent_launch_contained, MemoryStore.render_memory_digest,
  CollectionObserver.begin/observe/expectation/snapshot/_snapshot/_record/
  _persist/_seal; the directly used _identity_bytes/_identity_file_hash/_hash_metadata/
  _code_projection/_find_code/_serving_snapshot/_serving_revision/
  _check_serving_snapshot/_now/serving_observation/loaded_identity helpers;
  applicable first-party adapter build_argv and actual cached supervisor
  backend launch/finish are also fingerprinted. Sources are compiled
  without execution at actual import origins. Origin or loaded-source mismatch
  is unavailable, never repaired by importing another checkout.
- `files`: sorted canonical path/sha256 records from those actual source files,
  interpreter executable, actual installed package METADATA (bounded version
  header acquisition, with distribution location observed at initialization),
  teams/agent definitions, applicable provider registry and pinned
  binaries, and applicable adapter store/executables/dependencies. Contents,
  credentials, prompts, bodies, briefs and queries are never exposed.
- `teams_sha256`: canonical loaded team/manager/sorted-worker metadata hash;
  loaded registry is checked against current teams.yaml. `cohort`: sorted
  agent/team/role/executor/model records from actual registration/definitions,
  with null model retained rather than inferred.
- `profiles`: sorted name/kind/workspace_adapter_id/command_adapter_id/
  readiness_marker_fragment/model_arg_sha256/provider/adapter records from the
  actual runtime registry. Builtin provider is path/sha256; custom provider is
  null, adapter is id/version/contract_version/dependency_manifest_version/
  dependencies (sorted path/sha256 records, including the approved executable).
  Builtin adapter is null. Pending/missing/hash-mismatched custom adapters fail
  unavailable. No command text or permission surface is emitted or changed.
- `backend`: mode/name/version/capabilities. Legacy mode has null other fields;
  supervised mode uses only the already cached actual capability report, with
  capability-name -> guaranteed/best_effort/unavailable values. Absent cached
  report is unavailable; GET never probes/launches. This metadata is descriptive,
  and does not claim backend health or enforcement beyond its existing report.

Acquisition is zero durable writes and no reseal/full-history reconciliation.
Short observer admission remains nonblocking. Founder THR091seq557/559/560
permits at most0.5s CUMULATIVE DB-lock admission waiting across both revision
bookends and all attempts, clipped by the ORIGINAL enclosing acquisition
deadline. Nested/reentrant reads share admission and SQL accounting. DB ownership
is released before any observer metadata, loaded identity/file or network work;
a caller already owning the DB lock cannot request a serving observation.
Each attempt obtains a NEW complete opening snapshot/revision -> identity ->
identity -> revision/closing snapshot pair. Only concurrent-write revision or
observer movement retries; all original checks run again. Raw unsuccessful pairs
are charged and retained through existing bounded module logging; retention or
resource failure refuses. Accept one wholly stable pair, never splice halves.

Completed file, cohort, comparison and validation work is checked against the
same original deadline before successful serving/report/preparation/current-epoch
returns, including no-control and HTTP cutoff paths. Both full internal snapshots
must validate every required counter/digest/reference/seal field before movement
can retry; the abbreviated public boot projection has a separate validation mode.
Retry requires a full write and flush of the raw pair through an existing standard
synchronous stream/file log sink, with filters and formatting applied. Disabled,
filtered, unavailable, short or failed delivery without another acknowledged sink
refuses; a normal warning return is insufficient. Serialization and formatted
bytes consume the original aggregate budget. This establishes synchronous delivery,
not crash/fsync durability or hard OS-I/O timing. Deadline checks precede successful
read results and never reclassify an already successful publication COMMIT.
The ORIGINAL identity/source/boot/context fence survives attempts. Changed or
unavailable identity, cohort/profile/provider/path drift, observer replacement,
malformed state, sticky producer errors and other hard errors refuse immediately.
One original30s cooperative acquisition deadline, shared0.5s admission allowance,
original aggregate row/byte/closure/request/transport and SQL work allowances
span all attempts. The original two identity-read file/byte allowances are each
shared across retries, never renewed. SQL scopes share50M VM instructions,
100000 fetched rows/64MiB and3s cumulative cooperative SQL/admission time,
clipped by that enclosing deadline; time outside DB ownership is not SQL time.
Pending preparation/writer and any exhausted/unresolved acquisition withhold
data_through. A stable cutoff is evidence to a future validator, not health.
Work is limited to64 agents/teams (64 workers per team),96 regular files and
1GiB total per identity-read allowance across all attempts; declared
executables are streamed in at most 256 KiB chunks with a 512 MiB/file bound
(the actual registered Claude/Codex binaries exceed 200 MiB). Metadata/source/
registry/agent files are capped at 1 MiB, custom dependencies at 16,
profile model arguments at 16 strings of at most 256 characters. Nonregular
files, unreadable/moving files and exhausted limits are unavailable. Filesystem
operations have bounded byte/work admission, not a new OS-I/O deadline or timer.

This source view does not implement the admitted independent role/job acceptance
parser, atomic epoch/invalidation, BOTH health consumers, installed canary or
operational acceptance. Backend and canonical CLI remain insufficient_instrumentation;
thresholds_met/diagnostics_valid_for_collection/evaluation_candidate stay false.
No natural collection clock or epoch starts from identity, boot, census or source
test. Same-UID integrity is detective only, not attestation. Collection CLOSED,
epoch NOT STARTED, 48h handoff NOT DUE; frozen eligibility thresholds unchanged.

## Observation-only reporting core (TASK-9673 recovery / TASK-9559)

The backend and canonical CLI share the pure `memory_telemetry_report` reducer.
Founder THR-091 seq480 narrows the census to current boot/epoch activity plus
its referenced lifecycle, retry, delegation, QA/job and epoch/invalidation
records. All activity in that window is selected before attribution filtering;
a read/search now using an old retired SID retains the old task and interval.
Ancestry-only dependencies retain their parent/revisit graph facts; owners used
for lifecycle/proof retain original results, lifecycle and control history plus
direct revisits. Historical seals and SID-less intent/expectation/binding rows
from other boots have no current-census consumer. No prose/command ID scraping
or unrelated global same-SID audit/result search supplies an admission gate;
PR1084's supplied-liveSID trust limit remains. Same-owner contradictions still
refuse the strict failed-before-exposure class. Historical `memory_read` rows
retain their AGENT-scoped outer task_id; the owner is payload.task_id. Each
capture exhausts the existing action=memory_read read once and indexes literal
payload owners, including dependencies discovered later in the closure. Only
required full-history owners enter evidence; unrelated owners sharing a SID do
not. The complete scan consumes the existing row/byte/SQL/deadline/transport
budgets, including unrelated returned reads, and exhaustion refuses rather than
asserting absence. Closing and locked atomic rereads repeat this discovery, so
late preboot payload-owned membership cannot hide behind prepared IDs.
Native audit materialization reuses payloads only inside one invocation's
coherent SQLite snapshot. Completed window/action predicates suppress redundant
payload fetches in later overlapping queries; the original SQL filters, complete
columns, ordering and limits still select every matching row. SQLite NULL/type
and JSON filter semantics stay unchanged. Every new payload is strictly decoded
before selector exclusions, including consumer-free historical evidence; reused
records retain their exact values and consume existing serialization/work budgets.
SQL metadata rows/bytes/VM/time and the original shared deadline still count;
there is no second allowance or cross-call cache. Closing, pair-attempt and
atomic rereads start fresh. Unique evidence can still exhaust a bound and refuse;
lower duplicate bytes alone prove neither complete acquisition nor durable health.

Every required missing task,
result, job, lifecycle, control or genuine callback remains a refusal.

Observer, native database, existing HTTP readers, backend and canonical CLI,
acceptance preparation, current epoch references and the BEGIN IMMEDIATE reread
use the same scope selector. Closing capture and commit recompute membership,
including new direct revisits, root results and malformed/later controls, rather
than rereading prepared IDs. Original job streams/digests, native retry authority,
INTEGER callbacks, original admission age and observer→DB lock order remain.
File/network reads stay outside publication locks; any failed revalidation rolls
back. No-epoch diagnostics are current-boot observations and remain CLOSED;
preboot dependency records do not inflate their natural denominators.

Acquisition refuses beyond 100000 work records/64 MiB encoded work per capture
(including overlapping discovery/owner reads), 4 MiB per record, 4096 closure
steps, 8192 HTTP requests, 16 MiB raw response or 128 MiB transport bytes.
Paired HTTP captures share a 30-second cooperative deadline and twice the
per-capture record/byte work allocation. HTTP streams refuse nonidentity content
encoding, malformed UTF8/JSON/duplicate keys, cursors, ordering and incomplete
original output. A request has at most a five-second socket timeout and the
remaining acquisition budget; client refusal does not cancel an already-running
server query. Native SELECT/publication use the shared cumulative0.5-second DB-lock admission,
50000000 SQLite VM instructions and three-second cumulative cooperative SQL
allowance described above, all clipped by the original enclosing deadline;
publication checks the deadline again before commit. SQLite progress interrupts
SQL, not Python/OS scheduling. The same private budget encloses existing public
/audit (including optional enrichment), task list/detail/default recall and
keyed job/output acquisition BEFORE coherent/synchronized lock admission. Each
guarded server read shares nested scan/deadline work, caps SQLite busy waits at
100ms, rejects SQLite values/records over 4 MiB before Python allocation, and
charges at most 100000 fetched rows/64 MiB raw UTF8/scalar bytes before caller
JSON/model decoding. Native selection additionally charges encoded work above.
Resource exhaustion returns ordinary HTTP503, never a successful empty page.
Connection row factory, length limit, busy timeout and the private progress
handler are restored on exit; read views preserve caller transaction ownership.
The atomic writer still rolls back failed locked rereads and checks the deadline
before commit. Job output/receipt readers open each stream after releasing DB
locks, read at most max_bytes (up to10 MiB), and preserve original byte totals,
UTF8 replacement, truncation and missing-file semantics. Collection validation
independently refuses missing/incomplete/replaced/truncated output; commit uses
the already prepared validated bytes without file I/O under its DB lock.
Selected LIMIT/byte counts and sampled VM instructions are not SQL rows-examined
or hard RSS/wall-time guarantees. No schema/index/writer/new endpoint or response
envelope is added.

HTTP uses only existing /audit since/action/task filters and keyed task details,
ancestry recall, direct revisits and job/output reads. Positively referenced
probe jobs also select complete same-agent task candidates in the original job
window through existing assigned_agent task pages; probe roots retain all direct
children. Claimed receipt IDs alone cannot establish slot uniqueness. Roles
bracket the report.
Native snapshots are coherent; paired HTTP captures compare full membership,
raw records/outputs and ordered task/serving bookends using the relation above.
Only the three valid rendering timestamp paths have unary exclusions; heartbeat
and accompanying updated_at movement is a checked ordered-pair exception.
Malformed/unknown fields remain evidence. An unresolved moving/refused capture
yields acquisition_unavailable, CLI exit1 and no partial
stdout. Sizing/source checks do not qualify installed readers, repair a sticky
observation failure, accept B/C or start/reset an epoch.

D (THR-091 seq389–390) adds disjoint `by_session_kind` diagnostics: first_launch,
job_resume, retry, retry_job_resume, other_continuation, unknown. Actual runtime
`revisit_of_task_id` supplies retry lineage, never a name/brief; one genuine
`task_resumed_from_jobs` after an earlier same-task session and before this start
supplies job resume. Later sessions without this evidence remain explicitly other
continuations (or retries with a flag). Per-kind correlated counts reconcile
exactly to aggregate; no new per-kind threshold is introduced. Empty categories
report no observed population; synthetic operational snapshots are not natural
production acceptance.

Exact boot/ordinal/task/agent/SID joins establish runtime intervals. A SID-less
end attaches only when exactly one task/agent interval contains it, never by
latest agent. All natural and excluded recovery/thread/dream intervals establish
end ownership first; only natural starts enter classification/source validation.
Actual audit IDs establish order, including timestamp ties. End,
terminal and a later same-task start bound old SID eligibility; credited reads
and searches after that boundary are hard failures per kind, while earlier
history stays valid. Old/pre-epoch credited tuples are inspected even when their
invocations do not qualify for metrics. Missing/misjoined/duplicate lifecycle or
lineage yields unknown diagnostics and cannot produce healthy zero; still-active
exact current in-progress tuples need no fabricated terminal. Reports close
eligibility on damage and require the existing explicit reset process; they
never write invalidation/reset rows. Existing admission <=48h freshness and
no age-only ongoing expiry, 14 complete UTC days AND500 sessions, per-agent30,
strict activation<10%, NONshown-search>25% and aggregate/majority/pair rules stay
unchanged. These temporal diagnostics cannot identify the actual caller of a
foreign *live* SID. Memory get/search credit trusts the supplied live SID after
matching its live org/agent context and derives the task from that context.
Deliberately supplying another same-agent task's live SID therefore credits
that selected session. This is a known observational trust limit, not
authentication or hostile-caller proof.

Founder THR-091 seq401 declined `caller_task_id` and made explicit foreign-live-
SID rejection non-gating, with no new API requirement. A is scoped accepted
from completed JOB-4624's resume, retry, overlap, ended, absent, lifecycle-join
and cleanup results without rerun. The original JOB-4624 exit1/raw FAIL and
TASK-11402 QA REVISE remain unchanged historical evidence under the prior
criterion; this acceptance does not relabel them. Ended/absent selectors must
remain uncredited, POST-retirement read/search credit must remain zero, and
earlier legitimate active reads remain valid. D does not establish actual
caller identity. Restart, source merge and this documentation do not accept
B, C or an epoch; original admission/current full health, immutable epoch and
frozen observation requirements remain separate.

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
launch/expectation census. Without accepted G1 authority, trusted canary/epoch, census and probe
health remain UNKNOWN/unavailable; the current admitted collection reader below
revalidates them before claiming healthy collection. There is no collection/tuning, ranking write,
synthetic/natural eligibility claim, authority override or epoch start.
The read-side snapshot/two-sweep contract detects observed drift; it adds no
writer fence or hostile same-UID guarantee. Full eligibility, operational H-v1
and actual shipping/installed canary cases remain separately gated.


## Current G1 admitted authority and AGE correction (THR-091 seq277)

Earlier source-unit OPEN/null/continuing-age and unavailable-report descriptions
above document those historical unit boundaries. The current G1 behavior in this
section supersedes those source limitations; actual installed acceptance and
operational duties remain separate. G1 provides strict opt-in version1 role envelopes in exact
admitted summaries, with server-derived own IDs, mandatory external result IDs,
registered independent role/lineage joins, immutable prior finite plan/whole
brief/command, owned real terminal jobs, complete byte/audit/output identity and
ROOT/CHILD operation proof. Historical summaries and failures are untouched.
QA must also differ from every ROOT/CHILD maker named by that prior approved
finite plan; another registered worker role alone cannot prove independence.
The original ROOT and CHILD tasks must remain completed and uncancelled with
the exact maker/team binding. Successful earlier bootstrap operations cannot
retain positive authority after the probe task itself fails.

The manager candidate is observed after both successful existing result-log
arms using the actual row ID. Synchronized publication prepares bounded
file/output/current-identity evidence outside the DB reservation, then takes
observer→Database locks and BEGIN IMMEDIATE to revalidate records and append
new epoch/invalidation audit rows. Existing history, schema, creators, admission,
job custody, permissions and session lifecycle are unchanged.

AGE supersedes ONLY continuing probe-age expiry. Every probe's exact aware
UTC age must be between0 and48h inclusive at new initial/reset FINAL server
commit; a delay across the boundary refuses/rolls back. No client/report time
can backdate admission. After admission, both serving current_epoch_references
and the shared report reducer authenticate the original evidence against its
original commit and CURRENT complete source/import/loaded code/boot/cohort/
profile/path/applicability/census. Equivalent authenticated replay selects that
same original boundary and preserves its ID/time/projection, including after48h.
It cannot renew, switch QA, branch or fall back through malformed newer controls.

Current damage, drift, loss or partial/moving acquisition remains unavailable.
Withdrawal authenticates ownership/predecessor even when proof is stale or
unhealthy. Reset requires fresh independent QA and exact predecessor, retaining
old history and excluding old/pre-start tuples without window stitching. Both
report readers make zero durable writes. Frozen14 complete UTC days AND500
natural sessions/per-agent30/strict10%-majority/validated25%-role corroboration
remain unchanged. D08/D09 contradictory shown/search-source mixtures remain
arithmetic/contradiction negatives rather than source positives.

Implementation, source fixtures and a PR never grant production installed QA,
canary, official epoch or tuning. AFTER an actual independently accepted
production epoch, the root still owns the genuine durable independent health
check within48h, exact epoch/start/deadline/command/runtime/source/output/audit
receipt and eventual14-day/500 follow-through. Success does not renew acceptance.
Job-error, incomplete/in-flight or late receipts leave that duty unfulfilled;
only measured instrumentation failure closes via existing health predicates.
