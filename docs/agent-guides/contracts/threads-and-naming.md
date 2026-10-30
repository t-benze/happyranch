# Threads, routing and naming contracts

> Moved verbatim from the repository-root `AGENTS.md` on 2026-10-30. Sections keep
> their original relative order. Where a later section says it supersedes or
> extends an earlier one, the later section controls; sections marked
> "Historical" are retained evidence, not current instructions.

C1a/C1b private thread-delivery release source (THR317 seq25/34, ROOT12084)
has a dormant service bound to the actual initial app/state/runtime/org-map,
org/DB/path, supervisor/tracker and attachment generation. Only actual successful
ASGI/listener startup admits it; construction/readiness performs no operation or
selection. Native preview/show and the sole `release_exact_thread_delivery`
transaction have no ACTIVE HTTP/CLI, startup, reaper or automatic release caller.
Temporary, dynamic, custom, replaced and missing contexts refuse. Neither
ambient credentials nor caller proof flags enable mutation. Proof-backed release
is unavailable. The approved human risk override requires exact preview, stable
org-wide key, canonical command and explicit confirmation of late producer/session
writes, overlapping work, repeated external effects and missing owner/process/
effect closure. This source supplies no live-use, deployment or restart authority.

Startup and archived-thread resume cutover share a lazy, fully materialized
release view and common lifecycle-family decoding only within one transaction's
mutation-free segment. Unbuilt differs from proven-empty; every write invalidates
both views and selected conclusions. Initialized pairs still pass evidence and
permission checks before the no-op shortcut. Common sibling shape/size/duplicate/
org/thread/audit-owner checks remain complete; selected initial/append/native
agent/session histories replay on demand in caller order. Ordinary absent history
remains None, distinct from bounded release missing/orphan/1024-row refusals.
Permissions, live owners, holds, recovery, Pause, effects and raw census stay fresh.
Terminal status alone is unsafe: callback consumption can precede cleanup, and
settled=true/quiescent=false or sticky failed_cleanup retains unknown. Pending
preservation still uses the stricter retry/deferral/live-owner predicate; nonpending
history must be producer-settled and known before it stops preserving. Other
callers reread normally. This is full common decoding with lazy selected replay,
not unsettled-only pruning or lifecycle clearance. A legacy triggering_seq0 write
followed by another participant's native_delivery_range refusal rolls back the
whole cutover, without reusing the prewrite view or changing resume's prior commit.

The append-only version1 `thread_delivery_release` companion retains real thread
scope/permanent agent meanings and literal lifecycle-v1/unknown/retry history.
Complete bounded five-column and native readers bind source prefix, original and
optional genuine INTEGER replacement; invalid/foreign/orphan/duplicate/future or
incomplete evidence refuses. Only that obstruction's delivery interpretation can
change. Saved-prefix drift or late ORIGINAL lifecycle revokes current permission;
legitimate replacement history and the operation's own suffix preserve committed
history. Native recovery/runnable/reserve/claim, arrival/settlement, exchange
catch-up and separate probe republication share this interpretation. Raw host,
move/Pause/drain and diagnostic census remain blocking. A pending started REPLY
uses one interruption/slot-CAS/covering replacement commit without advancing ack,
resetting breaker/lease/held state or adopting old retries. Terminal history gets
no replacement;15402 gets no second catch-up. Unknown COMMIT requires strict
same-key readback, never remint/rekey. Old-reader/mixed-version operation and
downgrade after use are unsupported. Cooperative 3s/1024-row/4MiB/256-statement/
5M-step budgets include closing reads; exhaustion is unavailable, not absence or
a hard wall/RSS guarantee. C1-13 activation and C2 task-tail remain deferred;
12044/10693 remain unrecovered. All behavior/SQL/locks/races/cancellation/crash/
security proof is UNEXECUTED; integration INCLUDING callback is SKIPPED24342,
Python units RETIRED. Only lint/static evidence and independent full review plus
same-final-head scoped SOURCE QA can be attributed to this slice.

C1-13 dormant adapter source adds an UNMOUNTED four-handler router and an
UNCALLED `threads.register_release` with capability/preview/apply/show handlers,
private request custody and four client methods. Existing `threads.register`,
app mounts and serving owner stay unchanged: no supported installed release
tool, external_adapters_available=false and proof_backed_available=false.
Future request metadata derives from native strict DTOs. Original streamed UTF8
JSON is limited to16KiB before normalization; query/execution bindings refuse.
Bounded32KiB closed results/errors preserve committed=false/null/true and
no-store even on dependency errors; no synthesized202 or absence on failed read.
Preview/full-command digests are distinct. Explicit four-risk confirmation and
positive no-follow same-UID0600 file/0700 private-directory fsync/readback/close
custody precede exact-byte POST. Failures preserve bytes/key. Transport loss
allows at most one same-key GET and never another POST/rekey/poll; show never
publishes. Shared bearer/same UID cannot establish exclusive human presence.
Disconnect leaves native lifetime/outer retirement with the existing owner.
Fixture/native-denial/mount/registration activation remains HELD: the six shared
Web/Swift reader cases lack attributable RED/restoration/GREEN, with no waiver
or execution allocated. C1-13 public delivery and C2 remain pending; all adapter
runtime/security/custody/HTTP/CLI proof is UNEXECUTED, never test-authoring PASS.

C1b registers each operation and org native lifetime before submission, captures
every returned future before a cancellable await and owns outer retirement after
disconnect. Positive native return, rollback/settings restoration and outer
settlement alone discharge that guard. Revoke closes loop intake first and takes
only its C leaf for at most25ms; timeout is incomplete and bars host shutdown,
detach and DB-close fallthrough. Remove/all-close/runtime switch/direct org close
independently require positive C idle and renewed attachment/busy/hold checks.
Final order is move -> DB -> controller -> residue -> tracker -> C leaf -> the
existing COMMIT. All SQL inventory/decoding finishes before owner guards; only
COMMIT occurs inside, with reverse release before rollback/readback/notification.
The original3s budget starts before submission, includes both observation passes
and native+SQL work within5M steps. Under actual owner locks, built-in maps over64
refuse before iteration; each pass reserves n+1 rows,2048*(n+1) raw bytes and4096
native units per record/completion. Any residue/unhealthy measurement, relevant
retained ownership/binding/preparation or outstanding native writer blocks.
Org-level unresolved move/Pause controls conservatively refuse; no task root is
inferred from thread text or tracker absence. Fresh guarded CURRENT facts and
existing admission/release counters detect observed changes, without certifying
invisible historical tracker/residue ABA or old process/effect retirement. After
commit, independent current eligibility permits only SAME-token put_once through
the existing queue; notification loss never rolls back, remints or reapplies.

**Naming v1 core, API and routing source (THR-293 seq14/22/34).** One daemon controls each org.
OrgState admits the existing generic/workflow/canonical source before installing
an org-only, atomic three-table naming extension. Installer-only N-SAC-1 in
`runtime/identities/admission.py` checks selected/actual DB ownership, task/audit
shapes, closed naming interactions and unchanged workflow/Pause/move/naming
validators inside the existing transaction; it builds no whole-release reference.
Global workflow/legacy reference consumers remain strict and separate. Non-install
refreshes/readers keep their prior behavior. Finite synthetic verification uses
`uv run --frozen python scripts/verify_naming_structural_admission.py --case B-SA-01
--scratch NEW_ABSOLUTE_TASK_TMP_CHILD --evidence NEW_ABSOLUTE_EVIDENCE_CHILD`;
select B-SA-01 through B-SA-08 separately, per the fixture README. No test Python
imports, live DB input, repair, provider or daemon lifespan is supported.
Generic Database and
runtime-audit remain naming-free. Every naming-installed database, including F,
requires a compatible naming reader; explicit F/E/G/H/I workflow migrations preserve
naming rows/revisions and never install absent naming. Flat schema1 still refuses.
Current names and permanent former claims share the case-insensitive ASCII
namespace with present active/pending/terminated IDs and founder. Editable names
are 1–64 ASCII letters/digits/underscore/hyphen with a letter/digit first and no
trimming. An admitted `_worker` ID remains an owner-bound default/address; it is
not an editable-name exception or permission to admit longer IDs.

The internal rename service uses revision CAS and one atomic existing-shaped
founder audit; pure rename changes no task/session/workspace/permission/authority
binding. Supported lifecycle writers reconcile actual terminal canonical results
under their existing shared coroutine gate. Naming-unavailable blocks new ID
creation/rename, while original same-ID guards and callbacks keep their existing
behavior. Coherent naming never proves bootstrap, workspace rollback or cleanup
success. The bounded API source mounts org-local GET /identities, POST
/identities/resolve and PUT /agents/{agent_id}/addressable-name and
/founder/addressable-name. Strict rename requires only addressable_name and
positive expected_name_revision. Raw body/query task/session/agent/composer/
speaker/thread/invocation identity-key presence refuses before decoding, even
null/partial/malformed. Existing shared bearer cannot prove a human identity;
descriptive founder/name headers confer no authority. Reads preserve canonical
IDs and expose null/unavailable label metadata on naming failure; name revision
and canonical-definition revision are distinct. Resolution is diagnostic, never
authorization or reservation.

Routing source now classifies current names and permanent IDs case-insensitively
to canonical agent IDs using explicit prose @tokens. ASCII quotes remain prose; explicit blockquote lines are literal; email/word interiors, escaped tokens and supported code are literal. Any
recognized former token rejects the entire current
message/recipient input with409 former_name and current_name, before thread-ID
allocation, exchange closure, callback settlement/consumption, message/audit/queue
mutation or application attachment finalization. Composer, speaker, task/session
and invocation fields remain ID-only; labels confer no authority. Current names
of pending/terminated owners do not designate wake targets. Agent unknown/self/nonparticipant-only intentions retain conditional fallback;
Founder unknown/nonparticipant intentions refuse422.
Unavailable naming preserves actual ID behavior; non-ID addresses refuse503
naming_unavailable. No read adapter reconciles or writes names.


New-message body admission excludes email/word interiors, escaped tokens and
supported inline/fenced code; ASCII quoted prose remains eligible; explicit blockquote lines are literal. Literal-only
bodies perform no body naming lookup. Genuine body aliases fail closed on service
failure, including empty explicit recipients and per-token unavailability. Known
actual canonical IDs retain owner checks; ID-shaped aliases supply no evidence.
Observed Former refusal precedes later chunk failures. en/zh-CN service wording
asks for retry or a known intended-recipient ID, with no body rewrite, automatic
retry/send or locale-triggered effect. Historical messages/delivery and release
measurement keep their original interpretation. The exact finite scanner contract
is in the current identity-names core spec (THR293seq174).

**Current naming CLI/prompt contract.**
Operator `identities list/resolve/rename` uses existing API and name-revision CAS,
local validation and uncertain-failure readback without retries. Human targets
resolve current names to IDs; founder recipients retain `@founder`. Historical
ID filters and naming-unavailable ID access preserve owner checks. Every CLI
managed associated upload checks former body/recipient tokens first, using
explicit prose tokens (email/code are literal), without rewriting bodies or
claiming reservation/
rollback across HTTP requests. Task/manager/full/resumed/fallback/dream/wake/
schedule prompts receive current self/founder/ID-selected roster labels; metadata
failure renders ID-only context without writes or launch gates. Automation,
callbacks, delegation/chain/fanout/config/roles and provider/task/session IDs stay
ID-bound. The provider-aware UI/types, editors and name/ID pickers are present.
Source-pinned maker receipts record actual scenario5/8/9 E2E and frontend outcomes
in the current spec and maker handoff; independent acceptance remains separate. See the current naming spec and maintained guides for the
combined source contract and remaining behavioral acceptance gates.


Current-founder-only MESSAGE classification is ephemeral and separate from
empty agent mentions. It suppresses broadcast and frozen-cohort fallback for
that message, while full-recipient required watermarks still advance. Mixed
founder/eligible-agent mentions route the agents. Founder stays human inbox only,
never participant/executor. BOOTSTRAP/TASK_FOLLOWUP keep their isolated broadcast.
Already-due stale-exchange catch-up tokens remain returned and enqueued after
commit; they are not attributed to the new founder-only message. Restart reads
existing canonical mentions_json/ranges/tokens/exchange/deferral rows, without
reparsing historical names or discarding pending work. Frozen priority membership
comes from the opening message's persisted mentions; no member/receipt column
or table is added.

Filesystem namespace capture precedes the connection RLock; DB-only snapshot
revalidation precedes the transaction's first effect. Compose/send/reply and the
shared non-HTTP dream compose boundary revalidate actions. Multipart reads finish
before final admission and application allocation/store/write has no intervening
await. Framework-owned request parsing/spooling is not published attachment
storage. The existing one-daemon ordering applies; no nested coroutine gate,
extra namespace lock or filesystem-under-DB lookup is introduced.

THR293seq34/35 requires twelve readable E2E scenarios, not the historical233/297
case matrix. The finite naming selection includes routing bodies for5/6/7/9/12;
original manifests remain historical proposed work. Python units and their collection are
RETIRED under THR-291 seq40, never PASS and never a gate. General broken integration is
SKIPPED THR243seq42. Naming E2E uses a supported disposable GitHub runner or Mac
Linux guest with finite commands/resources and actual cleanup. Source-specific
maker receipts and remaining independent gates are recorded in the current spec
and maker handoff; source publication does not grant feature acceptance. This
combined candidate includes CLI/prompt and provider-aware UI/type source with
English and Chinese editors, labels and pickers. Supported frontend checks run
in the guarded worktree; exact command/runtime/source/exit receipts and remaining
source-pinned E2E/browser/schema receipts and independent gates live in the maker
handoff. Final whole-candidate
independent reviewer APPROVE, focused behavioral QA PASS, applicable checks/hooks,
normal publication and guarded manager merge remain. Source integration and
mock-backed frontend checks do not establish whole-feature acceptance.

See the [current naming contract](../../superpowers/specs/2026-10-09-identity-names-core.md).

- **GH-688 Phase 1 (thread reply-wake coalescing) is NOT deployed by merge.** Slices A/B (durable `thread_reply_delivery_state` + route/runner/API wiring) and C (web `reply_delivery` pair projection + audit lifecycle + this doc set) are merged code; the runtime only picks them up after a **daemon restart** (the deployment event). The release record/checklist lives at `docs/operations/gh-688-phase1-release-checklist.md` — it defines the deployment epoch, the two-org supersession-rate release gate (measured from the epoch, not merge time), and the post-deploy owner. Do not claim production behavior that only exists in the running daemon.
- **Thread rename/pin (THR-209 Phase 1).** Founder-only rename (`POST /threads/{id}/rename`) and pin/unpin (`POST /threads/{id}/pin`, durable `threads.pinned_at`) are presentation/organization controls. They must NEVER create a thread message/notification, touch participants/unread, or change activity timestamps; identity/URL/routing/lifecycle are immutable. Pins rank above unpinned (Pinned section, immutable numeric thread ID desc — THR-10 above THR-2, never activity/lexicographic) in open-thread views only; archived/closed views have zero pin presentation (one ordinary list, no split, no pin rank; durable pin retained for reopen/restore), and the status-less query + web "all" bucket are ordinary views too (no pin rank, no section); ordinary unpinned order stays unchanged. Audit via `thread_renamed`/`thread_pinned`/`thread_unpinned` rows (existing THR-* scope convention). See `docs/agent-guides/features-and-invariants.md` (Threads) and `docs/superpowers/specs/2026-08-25-thread-rename-and-pinning-design.md`.
- **Thread list paging (THR-289).** The same GET opts into live keyset pages with `page_size=1..100` (Web uses 50) and an optional scope-bound cursor. Each page returns org-wide Open/Archived/All/dream-origin totals, a sample time and lookahead continuation from one short owned read snapshot. Omitted pagination retains legacy `{threads}` behavior and limits. New pages use deterministic stored timestamp/BINARY-ID ties; All is globally ordered by start time and Open alone ranks pins. Browser refresh stages a replacement prefix to prior depth, commits once, rejects obsolete generations and retains usable rows/counts with updating/stale labels. Literal client search exhausts remaining pages; page failures require explicit Retry. Shared list ownership includes both real and prototype providers, and palette reads committed cache only. See the current THR-289 annotation in the Threads and Web specs.
- **Thread routing is UNCONDITIONAL (founder ruling TASK-6027).** Mention routing and the strict mention-led exchange are default and permanent — there is NO switch, route, CLI verb, UI control, or config key for either (all three were removed: the shipped per-thread `mention_routing_enabled`, the proposed per-thread `reply_exchange_enabled`, and the proposed org key `org_settings.threads.reply_exchange_enabled`). Mention routing (THR-198 Phase 2, merged Slice B): the `thread_messages.mentions_json TEXT` signal is derived server-side from `body_markdown` at write time (never client-declared) and the pure resolver `runtime/infrastructure/thread_mentions.py` routes every conversational REPLY wake at write time: one/more valid current-participant mentions → exactly that stable deduplicated set (speaker excluded); zero valid mentions (none, invalid/nonparticipant-only, self-only) → full broadcast minus speaker. The shipped `threads.mention_routing_enabled INTEGER NOT NULL DEFAULT 1` column is now an INERT legacy compatibility field: it must NEVER be read to alter behavior, written through a product surface, or exposed on the wire (persisted true/false values cannot disable routing after upgrade). Routing is write-time frozen (roster changes do not retroactively re-route minted wakes); TASK_FOLLOWUP/BOOTSTRAP are isolated and never mention-routed; GH-688 per-pair coalescing/claim/settlement/follow-on/rollback semantics are preserved. **Strict mention-led exchange (TASK-5966, founder-ratified THR-198 seq 194/195/196; design `output/TASK-5954/strict-hold-exchange-design.md`; `EXCHANGE_GRACE = 5 min`, absolute fail-open `4 h`):** additive exchange-epoch tables `thread_reply_exchange` (open/released/suppressed; partial-unique at-most-one-open-per-thread index; close_reason ∈ quiescence/max_priority_wait/founder_aborted/thread_archived/participant_removed/corrupt) + `thread_exchange_deferrals` (frozen D(E)) open on a founder-authored mention with P≠∅ and D≠∅. Inside an exchange the frozen cohort P(E) is the only wake set for no-mention messages (mention-pierces wake the mentioned agent without joining the cohort); deferred pairs get obligation-only `required_through_seq` raises (U0 — every conversational message raises `required` for EVERY recipient, separated from wake-mint eligibility) with no token and no wake audit. Closure = quiescence (no live cohort wake covering the exchange) + 5-minute grace, or the 4-hour reaper (fail-open), or suppression on abort/archive/removal/corruption — releasing every pair with unacknowledged content via exactly ONE slot-checked range-covering catch-up wake (`acknowledged+1..required`, contiguous, no fabricated acks). A pair whose live slot GENUINELY covers the released range (running `running_through >= required`, or any queued wake — its coverage is fixed at claim and `required` is monotonic) is coalesced (never a second token); a released deferral whose live slot does NOT cover it (a pre-arrival claim with immutable `running_through < required`) is durably marked `catchup_pending` and receives exactly ONE post-slot catch-up when the blocking wake reaches a TERMINAL outcome — failed/timeout settlement, or a recoverable restart terminalization — surviving daemon restart, never stranded (TASK-6057). Fail-closed recovery classifications (both-slots corruption, non-recoverable/invalid/malformed running or queued ownership) REVOKE the pending marker atomically in the same transaction and never mint a catch-up from state recovery declared untrustworthy (TASK-6065); the retained unacknowledged range is then served by the next conversational arrival (Phase-1 fail-open). Tightened D1: a wake claimed before a message never covers it (immutable claimed range + durable `started_at`/`enqueued_at` persisted in the settled audit payload `covered_from_seq/covered_through_seq/claimed_at/minted_at/exchange_held`). TASK_FOLLOWUP/BOOTSTRAP stay isolated (documented pierce source inside an exchange); daemon_restart is an interruption, never terminal, while a recovery replacement covering the exchange is queued/running (startup reconcile step 6e); corruption sweeps fail closed (suppress + diagnostic, never blind release). **Rollback is code-version rollback / operational containment only** — there is no user switch (TASK-6027): reverting the code stops new exchanges; an open epoch from a reverted deploy is closed by the normal quiescence/4h-fail-open bounds (never left stranded — the reaper, startup reconcile, and write seams evaluate closure idempotently). F1 measurement boundary `RATIFIED_EPOCH_F1` (Phase-2 epoch/window untouched; no historical backfill; any-burst deferral and the substantive-peer-engagement doctrine amendment remain separately gated — not implemented). Slice D's read-only release-measurement harness (`runtime/infrastructure/thread_release_measurement.py`, doc `docs/operations/gh-688-phase1-release-checklist.md` §8) reproduces the ratified Phase-2 acceptance metrics (mentioned-message saving 293/499→~24/204, founder coverage 698/0-loss, org-wide decline report-only) over the epoch `2026-08-26T14:25:23Z` with interim labeling until the one-month window completes. **Observation remains explicitly pending — do not claim analytics validation or a production result.** Do not revive the dead `thread_messages.addressed_to_json` (its separate cleanup plan is intact).
- **Prompt transport + thread provider-session lifecycle (THR-200).** The invocation prompt is delivered on **stdin** for claude (2.1.241), pi (0.84.2), codex, and opencode (1.18.25, TASK-6080 audit — pinned-version canaries: exact Unicode/newline semantics, `>=512 KiB` UTF-8 payloads); **Thread provider-session resume** is an optimization (never a correctness dependency; SQLite transcript canonical) for the executors whose contract is PROVEN against the installed CLI (TASK-5977, THR-200 seq 31): claude `--resume <id>`, codex `codex exec resume <thread_id> --json -` (same workspace-write sandbox + localhost network posture carried as `-c` config overrides since the resume subcommand has no `--sandbox` flag), pi `-p --mode json --session <id>` (fail-if-missing — never `--session-id`, which silently creates a fresh session), opencode `run -s <id> --dir <workspace> --format json` (stdin prompt; SAME project directory required — an opencode session is bound to its project dir and a different `--dir` hangs rather than failing fast; the thread workspace is stable per agent, and org relocation / workspace-path changes must invalidate stored opencode ids). All four re-emit the SAME stable id after non-interactive continuation and read the prompt via stdin (opencode large-prompt stdin verified live on 1.18.25: 606,099 bytes through a pipe). Registered custom-adapter profiles stay fresh-session — their resume contract is not part of the standard envelope (custom-CLI decision matrix, TASK-6080). Provider-declared session eviction transactionally combines the eviction audit and the durable `agent_session_id = NULL` invalidation BEFORE the full-prompt fallback; a failed fallback leaves the id NULL and the delivery watermark unadvanced. Eviction is classified ONLY from each provider's exact rc=1 stderr signature ANCHORED to the regex-escaped attempted session id (claude `No conversation found with session ID: <attempted-id>`; codex `Error: thread/resume: thread/resume failed: no rollout found for thread id <attempted-id> (code -32600)` — the observed complete line including the CLI envelope; pi `No session found matching '<attempted-id>'` — each verified 2026-08-28 to echo the attempted id verbatim, matched as the COMPLETE observed stderr line with only horizontal whitespace permitted) — or, for opencode 1.18.25 (TASK-6080 audit), rc=1 + EMPTY stdout + one complete LF/CRLF-delimited physical stderr line exactly `Error: Session not found` after ANSI-SGR stripping (the attempted id is NOT echoed; unrelated physical lines may coexist, but same-line prefix/suffix and cross-line assembly never match) — never from generic legacy substrings, cross-provider text, stdout-only text, wrong rc, wrong/missing id, prefix/suffix near-matches, a marker embedded in auth/quota/transport output, or ambiguous output, and never with more than one fresh retry. Archive, successful executor switch, and agent termination clear resume state (id NULL, watermark 0); each boundary is a database-owned transaction (reset + `thread_session_invalidated` audit atomic; termination reset+audit inside the terminate-cleanup transaction; archive wraps the status flip + resets + audit), a failed switch is rolled back so no new executor is installed, and a failed archive leaves the thread OPEN with every session row unmodified; participant removal deletes the row and its session state together (no redundant clear). The proven contracts do NOT require executor/model/config fingerprint invalidation, so no fingerprint column is added (EXCEPTION: an opencode session is bound to its project directory — resume is safe only for the identical workspace path, so org relocation / workspace-path changes must invalidate stored opencode thread session ids). The GH-688 claim gate stays strict (`<` resumes, `=`/`>` run the full prompt — no required sequence is ever omitted), and a resumed delta is authorized ONLY when the ENTIRE required post-watermark range is proven present and contiguous at the production seam (uncapped canonical load + independent authoritative max-seq query + sequence-completeness proof; truncated loads, internal holes, and claim ends beyond the transcript fail closed to the genuinely complete canonical full prompt), and a stored provider id whose durable delivery watermark is null/zero/negative (<= 0) is NEVER eligible for resume — it must make a fresh invocation with the complete canonical transcript; the equality state self-heals after one successfully settled full-prompt turn (no watermark-comparison change). TASK execution NEVER resumes provider sessions. THR-187/195 = evicted-id wedges; THR-198 = healthy-session equality wedge that needs only transport. See `docs/agent-guides/agent-executors-and-permissions.md`, `docs/agent-guides/orchestrator-contracts.md`, and `docs/agent-guides/features-and-invariants.md` (Thread Agent-Session Resume).

- **THR-225 task-followup replacement bound:** an authoritative pending
  `TASK_FOLLOWUP` owned by the dispatching team manager may transactionally
  create one true replacement root for its causal thread-dispatched root. The
  budget is persisted per lineage, never prompt-derived or per-turn; the
  replacement root and every supported descendant/revisit/chain/fanout
  followup are permanently fenced. Manager supersession is a separate path
  that rejects thread-originated roots and is not a THR-225 lineage shape.
  Ordinary reply/bootstrap dispatch is
  unchanged; queue notification occurs only after the atomic task + invocation
  marker + system-message + audit commit. No task column changes meaning.

**Thread reply provider breaker (THR-200 PR B).** One durable continuity per `(thread, agent, executor/model/policy)` opens after exactly three consecutive structured final provider-execution failures. Threshold 3 and cooldown 900 seconds are fixed policy constants, not environment/config settings, so restart cannot fork continuity or bypass an OPEN episode. Only a process proven launched and ending as provider nonzero, provider timeout, or post-launch contract failure qualifies; pre-launch/admission/host refusal, interruption/cancellation, canonical transcript gap fallback, exact eviction itself, successful eviction fallback, and a successful no-callback nudge do not. A failed eviction fallback counts exactly once. OPEN retains/coalesces the delivery range and launches nothing. After exactly 900 seconds the daemon scheduler atomically mints one HALF_OPEN probe; every tick also republishes a committed pending probe token through a process queue whose deduplication ownership spans queued and in-flight delivery. Worker success acknowledges that ownership, while a pre-claim exception or cancellation releases it for a later tick; the durable claim CAS preserves exactly-once provider launch. A recovered ownerless gap with no episode follows the same cooldown-probe path, never ordinary CLOSED admission. Held deferrals in an OPEN exchange remain held and excluded even when the breaker is otherwise recoverable. Probe success settles and closes; probe failure rearms 900 seconds. Episode/receipt/audit writes are idempotent and redacted; stale callbacks cannot close newer continuity. No API/OpenAPI/web projection or manual action is added by PR B.

## Functional naming UI and API contract

The existing org surface has an independent human-founder name editor. Active
agent detail, pending enrollment and terminated identity surfaces expose agent
name editors, keeping immutable ID and definition settings separate. Editable
names use exact ASCII 1–64 grammar (first letter/digit, then letters/digits/_/-),
without trimming. PUT sends only `addressable_name` and positive
`expected_name_revision`. A stale conflict performs authoritative GET, retains
the draft and requires explicit resubmission. An ambiguous response reads back
and reports the observed name/revision or uncertainty, with no blind retry or
claim that failed transport rolled back a commit. Absent/unavailable metadata
cannot rename. A displayed human label provides no authentication proof.

Provider-aware naming hooks share one org-scoped identity list/query. Successful
rename/readback refreshes identity metadata, preserving canonical IDs, settings
and unrelated drafts. Current labels display Name · ID (ID alone when equal or
unavailable). Bounded Founder exception (THR293seq236): the Sidebar account row
and thread list/detail/message-author/participant-rail sites show the Founder's
ready current name alone with matching avatar initials, else the canonical
`founder` ID (unnamed, loading, unavailable, error or another org still loading);
agents and every other label keep Name · ID. Historical IDs, links, keys,
owner/filter/recipient values and
stored message text remain canonical/literal. Metadata follows existing focus/
reconnect behavior and bounded 30-second polling; availability is never a global
gate for established ID operations. Editor drafts are scoped by org/kind/ID and
survive locale changes and identity refetches.

Real thread compose/follow-up/invite/forward and task read-filter pickers search
current names and permanent IDs. Selected values stay IDs across renames. Thread-detail
composer @mention hints offer only eligible participant agents, including their
current Name · ID labels and permanent-ID matching; human Founder is never
suggested, even after renaming. Manually typed current Founder names and
`@founder` remain literal and retain existing preflight/send behavior. Human
recipient controls elsewhere still offer the separate Founder option. Clear
invalidates pending task-filter resolution; stale success/error/finalization cannot
reapply a cleared filter, show an obsolete alert or release a newer attempt. Founder
is a separate human option routed as established `@founder`, never an agent
participant or task owner. There is no task-create owner GUI in the present web
source; the existing task agent filter is the relevant selector. Prospective
invite resolve omits `thread_id` because that optional scope means existing
participants; the actual invite endpoint retains final action eligibility.
Recognized former body/recipient tokens stop UI-managed associated attachment
uploads when caught by separate preflight. Body intentions follow the THR310 caller policy below,
with ASCII quotes eligible, explicit blockquote lines literal and email/code/escaped tokens excluded. This read
is not a
reservation: final server admission may reject after an intervening rename, and
already-completed uploads are not claimed rolled back. New en/zh-CN text uses
existing controls/tokens. The naming browser adapter captures 390×844/1440×900
light/dark states in en/zh-CN. Source-pinned captures and interaction receipts are
maker evidence; they do not establish pixel fidelity or independent acceptance.

The four existing naming adapters mirror IdentityView/ResolveResponse/RenameBody.
The repository contract snapshot is a summarized route/parameter/status artifact,
not full JSON Schema. Its four entries were originally authored from source;
the supported standalone generator and genuine served-schema comparison verify
that summary independently. Source-pinned schema/frontend/browser receipts live
in the maker handoff; final-candidate independent review and behavioral QA remain
separate requirements. The shipping-page MSW assertions in
`web/src/test/identity-names.test.tsx` add UI portions of scenarios2/3/4/5/8/10;
API assertions are `web/src/lib/api/identities.test.ts`; scenario9 also exercises
canonical skill-row attributes and stable row identity during label refresh.
These provider/component tests use frontend mocks. Source-specific commands,
red/keeper results and actual execution status belong in the maker handoff;
frontend results do not establish daemon/DB end-to-end behavior. Existing
twelve-scenario integration bodies/fixtures are preserved. Python units remain RETIRED under THR-291 seq40;
general integration remains SKIPPED THR243seq42. Published source and maker verification do not establish independent acceptance,
merge, deployment or whole-feature completion.

Naming namespace capture includes active/pending executable agent managers and
workers. Mainline typed human Founder managers have no agent definition and are
excluded from that agent set; Founder remains the separate human naming owner.
The combined I/roster/naming behavior remains unverified; see the current naming spec.

## Literal text and recipient intentions (THR310 seq3/13)

All Founder-authenticated writes, including an unbound agent-launched CLI, reject
unknown/nonparticipant body intentions with422 and eligible current-name/ID guidance.
Genuine Former anywhere in To/body retains409 before body422; unknown explicit To
retains404 and unresolved aliases retain503 under naming failure. Preserve native
structural/auth/binding/lifecycle/file errors. To alone selects participants (plus
a genuine agent composer). BODY suggestions are eligible selected prospective
agents in a new thread, current participants in an existing one; Founder is human,
never an agent participant or BODY suggestion. Manual `@founder` remains valid.

Validated agent writers retain compatibility; CLI stderr describes ignored tokens
and canonical narrowed targets or conditional fallback: recipients minus speaker
outside an exchange, frozen cohort intersect recipients minus speaker inside one,
possibly empty. Prior catch-up is independent; pending_replies proves neither
audience nor storage/launches. Direct agent API/internal authors receive no warning
channel (accepted limitation). Complete valid JSON-only bindings count for CLI
warning/upload attribution; partial/invalid bindings retain native refusal. No
response warning field/header/auth change or preflight reservation is introduced.
Founder-only suppresses new selection while preserving obligations/prior catch-up;
Founder+unknown differs, and self-only/quote-only use ordinary conditional fallback.

Only start, finite whitespace, or `(` `[` `{` ASCII single/double quote opens an
unescaped ASCII token. Explicit physical lines with0–3 ASCII spaces then `>` are
literal; unprefixed lazy continuation and four-space indentation are outside this
quote rule. Top-level unclosed fences mask to EOF; quoted ticks/fences cannot own
later notes. The precise finite whitespace/code grammar and examples are in the
identity-names core spec's THR310 section; this is not a full Markdown parser.
Nonblank authored bodies remain exact through storage, including controls,
indentation, CRLF/bare CR and trailing whitespace. The existing strip emptiness
predicate and attachment-only null outcomes remain; subjects/history are unchanged.

Forwarders quote every physical source line/header/blank/marker. Generated source
separators may become LF; notes remain verbatim, including CLI note-file newlines.
CLI's fully serialized quoted excerpt is at most4096 UTF8 bytes, metadata/prefixes/
newlines/marker included; separator and note excluded. Reserve the quoted marker
and retain the longest valid source prefix. Web retains its last-message selection
and size policy. Raw occurrence ranges keep duplicates; admission spellings dedup.
Insertion replaces only the prefix through caret, preserving suffix/spaces/focus.
Thread-body rendering styles only proven exact raw intervals, noninteractively;
ambiguous transformations stay plain, code/Mermaid stay undecorated, task/KB defaults
and historical delivery stay unchanged. No render-time naming or recipient inference.
Shared data is protocol/thread-mention-corpus.json (51 lexical +13 write-path rows).
Public-unexposed mentions_json, authentic debt/outage and genuine task/dream/callback
proof require actual evidence and cannot be inferred from static/mock checks.
