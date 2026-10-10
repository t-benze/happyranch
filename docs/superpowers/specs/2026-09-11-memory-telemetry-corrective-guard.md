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
epoch authority. The seal-action serving source view is described here. G1
durable acceptance/B1, both health consumers,
installed canary/deferred executor/population coverage and full reporting remain
unimplemented here. Existing backend and canonical CLI guards remain
`insufficient_instrumentation` with `thresholds_met=false`; no collection/epoch
transition, deployment, tuning or 48-hour clock starts from this source unit.

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
Nonblocking short observer/Database lock admissions preserve observer -> Database
ordering; no Database-held callback acquires observer lock, and file acquisition
holds neither lock. Two identity reads plus observer semantic and database
revision bookends must match. Pending preparation/writer, sticky errors, failed
reads or movement withhold data_through; stable cutoff is evidence to a future
validator, not collection health. Work is limited to 64 agents/teams (64 workers
per team), 96 regular files and 1 GiB total per identity read; declared
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
