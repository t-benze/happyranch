# Shared Artifacts ownership, retention, quota and audited system cleanup

Status: proposed design for engineering_manager review; **not implementation or
activation authority**. TASK-18710, parent TASK-18707, root TASK-18703. Founder
approval: THR-392 seq13/14, incorporating Product's seq12. Product contract
wording remains owned by product_lead (THR-394).

Source baseline: freshly fetched `origin/main`
`47709e5e718047fe4ae74e187caeec918d7af274`. This PR adds this specification
only. No executable code, migrations, tests, builds, E2E triggers, deployment,
restart, cleanup or configuration changes. `web/` and `e2e/` belong to the
sibling page-removal slice and are excluded here.

## Assumptions & open questions

The following are explicit proposals, not observations of shipped enforcement.
Implementation must wait for manager acceptance of this design. Items marked
**STOP** block the affected implementation; this document does not silently
choose an exception to an existing invariant.

1. Retention uses UTC instants and elapsed 24-hour days: default 90 days;
   reference/design source at most 180 days. The proposed interpretation of
   5 GB is **5,000,000,000 bytes**, warning at **4,000,000,000 bytes**. Manager
   must confirm the unit interpretation; only Founder can change the approved
   capacity or percentages. There is no permanent exemption.
2. Existing org bearer authentication and org isolation remain unchanged.
   `agent` currently supplies audit attribution, not authenticated per-agent
   ownership. Proposed renew/restore access is the existing org-bearer access:
   any caller already authorized to use that org's file API may act, recording
   its named actor and continuing use. Operational responsibility belongs to
   the owner, its manager, or Founder. **STOP** if Product requires enforced
   owner-only access: that is a separate authentication/access-check decision;
   an agent string must never be treated as proof. No auth weakening is proposed.
3. **STOP — canonical custom-skill content:**
   `runtime/daemon/routes/custom_skills.py::_write_artifact` writes immutable
   B2 version sources into this same directory. Workspace materialization in
   `runtime/orchestrator/workspace_adapters.py::_build_custom_skill_canonical_specs`
   and `runtime/daemon/routes/skills.py` recovery read those keys. Expiring them
   solely because no task/thread link is live can break documented skill
   recovery/materialization. A permanent prefix exemption would contradict the
   approved policy. Manager must commission/approve a compatible source-storage
   disposition before these keys can be classified or destructive cleanup can
   activate. This spec neither moves canonical content nor adds an exemption.
   Preview must report these dependencies explicitly as unresolved, counted
   bytes; it cannot certify them safe to expire.
4. **STOP — thread creation and human uploads:** CLI `compose --shared`
   currently uploads before a thread ID is allocated, with `thread_id=None`.
   Founder compose passes `agent="founder"`, but Founder is a human, not an
   owning agent. Required composer-agent/thread metadata cannot be derived as
   stated for these cases. Proposed agent solution is server-side shared
   multipart compose, allocating the real thread ID within the existing
   creation transaction, then writing metadata/attachments with rollback
   compensation. Manager must accept that extension and decide the accountable
   owning agent for Founder-originated shared uploads; do not invent an agent
   named Founder, guess a recipient, create an empty thread first, or accept a
   temporary `thread-draft` origin. Existing private compose is unaffected.
5. Proposed exact-name overwrite preserves today's active-file compatibility;
   expired generations remain explainable through optional generation links.
   Manager must accept the generation policy below before implementation.
6. Proposed notice destination is a durable system-message thread per owning
   agent, plus one org storage-management thread visible to the responsible
   manager and Founder. These reuse the existing Threads inbox, not a new
   notification product. Missing/retired owner or unresolved manager falls back
   to the org storage-management thread, recording the routing reason.
7. No fresh live inventory was made in this task. Approximately 1.7 GB is the
   THR-392 snapshot, not a measured day-one total. The sibling reviewed inventory
   supplies exact names, hashes, sizes, owners and decisions. The import and
   destructive switch remain gated on that review; a docs PR cannot satisfy it.

## Current behavior and compatibility constraints

Read before drafting: `runtime/daemon/routes/artifacts.py`,
`runtime/infrastructure/artifact_store.py`, `cli/commands/artifacts.py`,
`cli/client/client.py`, the CLI/route/DB thread attachment path,
`AuditLogger.log_artifact_put` and `log_artifact_delete`,
`runtime/infrastructure/db/schema.py`, `workspace_cleanup_scheduler.py`, and
the [nightly disk sweeper design](2026-10-15-nightly-disk-sweeper.md).
KB `engineering-team-workflow` and `shared-artifacts-hygiene`, and THR-392/394
were consulted. The current `CLAUDE.md` is a compatibility symlink to `AGENTS.md`;
the former load-bearing descriptions now live in maintained guides, notably
[Shared Artifacts](../agent-guides/features-and-invariants.md#shared-artifacts)
and [retired opaque-failure routing](../agent-guides/features-and-invariants.md#session-timeout-auto-route-retired--task-3604).

Today the store is filesystem-only. `POST /artifacts` creates or atomically
overwrites an exact name. API fields are `name`, `agent`, and multipart `file`;
list returns name/size/mtime. GET misses return a bare artifact-not-found 404.
There is a bearer-protected DELETE API, although no CLI delete verb. The
per-file limit remains `10 * 1024 * 1024` bytes; nested-name validation,
case-collision refusal and org scoping must survive.

The generic `ArtifactStore` also serves custom-skill sources, so metadata
enforcement must have an org-artifact service owner rather than being silently
bolted onto every generic `put/read/delete` call. Its direct callers must be
accounted for, including custom-skill compensation and schema retirement cleanup.

`audit_log` columns remain `id, task_id, agent, action, payload, timestamp`.
`task_id` already doubles as a scope ID. Artifact actions use
`artifact:<name>`, config actions use `config:<section>`, thread actions may use
real `THR-*`, and scheduled disk summaries use empty task scope / actor `daemon`.
Never replace an artifact scope with the originating TASK/THR ID, add a new
meaning to an old prefix, rewrite history, or alter this column/schema.

No changes to task terminal predicates, opaque-failure handling, parent-wake or
`_maybe_post_thread_followup` are needed. Preserve their existing call order.
Storage errors must return from the artifact boundary; they must not be wired
into task-failure/revisit scheduling. The existing custom-skill INSERT-before-
blob-write and compensation-through-COMMIT contract must remain intact.

## Metadata and additive storage model

Install org-owned SQLite tables through idempotent additive schema setup, using
`CREATE TABLE/INDEX IF NOT EXISTS` and explicit layout validation before use.
Do not rename, rebuild, drop or reinterpret existing tables/columns. Do not
copy thread IDs into `tasks.id` or infer ownership from filename prefixes.
Fresh and existing org databases must converge on the same new layouts.

| New table | Key and required content |
| --- | --- |
| `shared_artifacts` | `name TEXT PRIMARY KEY`, current generation, row revision. Validated exact POSIX artifact name is the public key. |
| `shared_artifact_generations` | Composite `(name, generation)` key; owning permanent agent ID (nullable only for legacy), originating task ID or thread ID (exactly one for classified rows), purpose, retention class, reference reason, created/renewed/expiry UTC, byte size and SHA-256, state, expiry/recovery/deletion timestamps, deletion/hold reason, classification review ID. |
| `shared_artifact_operations` | Unique operation ID, name/generation, expected revision/hash, operation/phase, reserved bytes, intended before/after state, filesystem identity, timestamps and failure/uncertainty. Durable journal bridges SQLite and filesystem effects. |
| `shared_artifact_notice_receipts` | Unique `(name, generation, expires_at, notice_kind)`; destination thread/message sequence, delivery state, outbox ID. Renewal to a different expiry creates a new notice identity. |
| `shared_artifact_org_state` | Singleton quota crossing latch/sequence, usage reconciliation status, rollout start UTC, last completed daily window and preview acceptance citation. No configurable override of Founder-reserved numbers. |
| `shared_artifact_inventory_reviews` | Immutable reviewed inventory digest, reviewer permanent ID/task/ruling citation and approved exact item decisions. Legacy classification cites this review. |

`active`, `recoverable`, `deleted`, and `legacy_unclassified` are persisted
states. `expired` is the read projection of a classified active row whose
expiry has passed before the next sweep. `protected` is a reasoned overlay on
active/expired/recoverable content, not a new unbounded retention class. A
generation tombstone survives backing-byte deletion; it retains only metadata,
reason and provenance, not file contents or local source paths.

Expired bytes may remain at their original literal path through recovery; the
service denies ordinary downloads. This avoids an unnecessary move/duplicate
copy. Internal operation staging must be on the same filesystem, non-public,
counted/reserved, and hidden from list/download. No symlinks or hardlinked
aliases may be adopted for destructive work. Check literal ancestors, regular
file identity, containment, size and hash before unlink; disagreement holds.

### Mixed versions and reconciliation

An old daemon ignores additive tables and can still write unclassified blobs;
it does not become metadata/quota compliant because the schema exists. A new
daemon discovers any file without a matching committed generation as
`legacy_unclassified` with null expiry; it never manufactures a 90-day clock
from mtime. A recorded generation whose bytes/hash changed under an old writer
is quarantined as uncertain, counted and protected from deletion pending
reconciliation. Missing known bytes remain explainable, not silently 404.

Do not relocate existing directories in a schema migration. Reconciliation is
a separate bounded service pass, safe on old directory layouts; an incomplete
pass blocks uploads/destructive work with `503 artifact_usage_unavailable`,
while GET/list of known records continue. Pagination/caps must report
incompleteness, not treat unseen files as zero usage or no dependencies.

Safe downgrade means schema remains readable to old code; old code cannot
enforce new tombstone/expiry semantics. Reverting the daemon requires an
operator-controlled rollback with storage mutations disabled, not simultaneous
old/new writers. Destructive activation requires exclusive new-daemon ownership
under the existing daemon lifecycle lock and a complete directory/DB reconcile.
Never claim old-daemon/new-row policy enforcement. No live rollback/restart is
authorized by this design PR.

## Upload, replacement, renewal and restore contracts

All URLs below have prefix `/api/v1/orgs/{slug}` and retain the existing bearer
dependency. Values are validated server-side before reading/writing durable
bytes. All automatic/system writers must use the same service admission seam.

| Surface | Contract |
| --- | --- |
| `artifacts put FILE --agent ID (--task TASK-ID \| --thread THR-ID) --purpose TEXT --retention {default,reference}` | All listed flags required, exactly one same-org origin, nonblank purpose. `--agent` is the named owning agent and audit actor; new references require `--reason TEXT`. Optional `--expires-at UTC` selects a shorter duration. Without it the server computes 90/180 days and returns the expiry. |
| `POST /artifacts` | Keep multipart file and query-style metadata, adding the same owner/origin/purpose/retention/reason/optional expiry fields. Reject missing metadata with `422 artifact_metadata_required` and `missing_fields`; reject bad origin/class/expiry with `422 artifact_metadata_invalid` and field reasons. No filename/mtime defaults for ownership or origin. |
| `artifacts renew NAME --agent ID --continuing-use TEXT --expires-at UTC [--retention CLASS --reason TEXT]` / `POST /artifacts/{name:path}/renew` | JSON API body carries the same fields. Expiry must be strictly after server renewal time and no later than renewal time + 90 days (default) or + 180 days (reference). Reference still requires named owner/reason. Keep original provenance; save continuing use and renewal history. Downloads/listing never change expiry. |
| `artifacts restore NAME --agent ID --continuing-use TEXT --expires-at UTC` / `POST /artifacts/{name:path}/restore` | Same retention limits, reason rules and bearer access as renewal. Restore only while recoverable and before `recoverable_until`; verify intact bytes, then atomically renew to active. No quota credit is consumed twice: recovery bytes already count. Deleted bytes cannot be restored. |

API registration must place specific lifecycle routes before any conflicting
catch-all name route and preserve names with nested path segments. Missing
fields should have a stable error code as well as readable text, including
model-validation errors. Unknown origin/owner rejects before mutation; origin
validation is provenance validation, not a new access grant.

For an active exact-name overwrite, retain the current behavior of replacing
bytes. Require the full new metadata again; allocate a new generation and
preserve prior generation metadata as a tombstone with reason `replaced`.
No prior-byte recovery promise is added to ordinary overwrites. Serialize
replacement, quota admission and reference checks; refuse replacement of a
verified live dependency with `409 artifact_live_dependency`. Identical retries
need an operation/idempotency key to avoid a second generation/event. Preserve
existing case-alias refusal. Reuse of expired/recoverable/deleted names is
refused with `409 artifact_name_retained`; use restore or a new name. Legacy
names cannot be overwritten to bypass reviewed classification.

GET without a generation continues to resolve the current name. New responses
also return a stable `?generation=N` download URL; supplied generations resolve
their own tombstones. Existing unversioned links cannot identify an older
overwritten generation, which is already true today; do not claim otherwise.
Thread attachment metadata can retain generation in an additive field for new
links; old records continue resolving by name.

The existing DELETE API must enter the same audited recoverable retirement
path, preserving metadata and quota accounting, rather than unlinking behind
the lifecycle service. It may not bypass live references, preview mode or the
legacy review gate. Its response explicitly reports `state=recoverable` and
the recovery deadline. This change to today's immediate delete is part of the
proposed retention behavior and must be covered in implementation review.

### Thread shared attachments

Existing-thread `send/reply --shared`: derive owner from the composer permanent
agent ID, origin from the real thread ID, purpose exactly `thread attachment`,
retention default and expiry upload time + 90 days. The daemon verifies the
derived values against the message context; no user-supplied owner spoofing or
missing-metadata bypass. Explicit reference-class retention requires an ordinary
metadata-complete put, then attachment by name/generation. Ordinary private
thread-scoped attachments keep their separate storage/access contract.

Compose must solve open question 4 before its shared path ships. With the
proposed multipart solution, no blob becomes publicly visible before its real
thread/origin record commits. Failed thread/attachment creation compensates
only this request's newly written bytes, including COMMIT failure. Journal any
uncertain compensation; do not delete a pre-existing or concurrently committed
blob. `_normalize_attachments` must use lifecycle lookup so a filesystem file
alone does not validate an expired attachment. New links pin a generation;
historical attachment rows and messages remain intact.

## Retention, recovery and verified dependencies

At upload/renewal, `0 < expiry-now <= class maximum`. No class, flag, reason or
download grants perpetual retention. A daily sweep first classifies each
eligible generation using current metadata and verified references:

| Condition | Result |
| --- | --- |
| `legacy_unclassified` or unresolved/mismatched metadata | Keep bytes; report classification/uncertainty. Never infer expiry. |
| Before expiry | Remain active; send due notice as specified below. |
| At/after expiry, live reference or failed dependency check | Keep bytes, mark protected reason and reference IDs; count toward quota; recheck daily. Expiry stays fixed. A positively live reference keeps the file downloadable with `protected=true` and its hold reason. An indeterminate check cannot authorize access to an otherwise expired file. |
| At/after expiry, positively no live reference | Transition to recoverable, set `expired_on=expires_at`, `recovery_started_at=actual transition time`, `recoverable_until=recovery_started_at + 7 days`. Late sweeps never shorten the seven-day recovery. |
| Recoverable before deadline | Ordinary GET explains expiry; restore is allowed. A newly verified live reference holds deletion and is reported for restore/renewal; it does not silently reset the clock. |
| Recoverable at/after deadline, positively no live reference | Delete verified backing bytes, retain deleted tombstone with actual deletion time/reason. |

A protected dependency is a temporary liveness hold, not a permanent exemption:
no expiry extension, no quota exclusion, and no automatic owner renewal. Once
the hold ends, a fresh full recovery interval precedes deletion if it had not
already begun. A recovery interval that already elapsed is not restarted just
by a hold; the next destructive pass still requires fresh negative references.

### Exact reference definition and fail-closed check

The authoritative scan reads the complete same-org SQLite task/thread corpus,
not a filesystem name prefix or a recent-page sample:

1. Live tasks are `pending`, `in_progress` (including delegated/job waits), and
   conservatively `escalated`; queued tasks are included before process launch.
   Parse `tasks.brief` and `tasks.final_output_summary`, and all
   `task_results.output_summary`, `decision_json` string values and
   `risks_flagged` belonging to that task or its direct children (parent-wake
   inputs). Follow explicit same-org TASK-ID tokens in those fields to the
   referenced task's brief/final summary/result fields, to closure. A visited
   set bounds cycles without discarding their already scanned references;
   missing/unreadable required handoffs make absence indeterminate. Private
   task attachment `storage_key` is not itself a shared artifact reference.
   This is a definition of explicit stored dependencies: arbitrary files in
   output_dir, binaries and unstated future use are not scanned. Task authors
   must put a needed shared key/link in the brief or handoff text.
2. Open thread means `threads.status='open'`. Join all its
   `thread_message_attachments.artifact_name` rows (nonempty shared names only),
   and parse all message `body_markdown` and declared attachment-bearing system
   payloads. Private `thread_attachment_id` values do not alias shared names.
   Archived thread links are historical; GET still explains their tombstones.
3. Recognized text forms: `[artifact:NAME]`, exact validated artifact-name tokens
   delimited by whitespace/Markdown punctuation/code quoting, and same-org
   `/api/v1/orgs/SLUG/artifacts/NAME` URLs (relative or this daemon's configured
   origin), optionally generation-qualified. URL-decode once, run existing
   artifact name validation and compare the complete binary-spelled key, never
   substrings or case-folded guesses. An unversioned match protects the current
   generation; a versioned match protects that exact generation. Do not scan
   arbitrary local paths, guessed extensions, image contents or remote sites.
4. A complete, fresh negative is required both at expiry and immediately before
   unlink. Under the org DB lock, take a coherent read and revision bookends,
   then serialize final metadata transition with task/thread reference writers.
   Do not release that writer fence between the final negative and committing
   the transition; bounded filesystem work must run off the event loop without
   allowing a reference publication to race the destructive decision. A writer
   attempting to attach a recoverable/deleted generation receives explanatory
   refusal. Daemon/external ownership uncertainty holds the whole affected pass.

Malformed relevant reference data, unavailable DB/queue projection, unknown
status, incomplete pagination, decode failure, missing required handoff,
deadline/byte/row cap exhaustion or changed snapshot yields `indeterminate`,
never `no references`. Retain affected bytes, audit `artifact_protected` with
reason `dependency_check_unavailable`, and surface incomplete scope. Raw text
with no recognized artifact form is not a positive match; a parser failure on
a recognized form is indeterminate. Reference scanning has no authority to
delete task results, messages, private attachments or canonical skill content.
The custom-skill conflict in open question 3 is an additional activation hold.

## Notices and quota

Use existing Threads system messages and inbox listing because they are
durable, visible to the owner/Founder and do not need a new notification table
or transport. Persist exact thread IDs in notice receipts, never locate notice
threads solely by their subject. Create/reuse threads with verified system
provenance and owner participant; Founder remains a human inbox recipient, not
an agent participant. Post a readable body with name, owner, purpose, expiry,
recovery deadline and concrete renew/restore command. Do not enqueue an agent
reply merely to read a routine notice. Unknown system kind renderers must have
readable body fallback; no web changes are assigned to these slices.

The expiry notice becomes due at `expires_at - 7 days`. The daily pass claims
one receipt per generation/expiry and commits the system message, sequence and
`artifact_notice` audit atomically with that receipt/outbox delivery. Restart
replay cannot duplicate a visible notice. Unavailable delivery is retained as
pending and audited as failure, never marked delivered. Uploads/short renewals
whose expiry is less than seven days away get the same notice immediately.
After downtime, send the overdue notice with its true late timestamp; no false
claim of seven-day advance delivery. Renewal changes the notice identity.
The existing `append_thread_message` owns and commits its own transaction;
do not call it inside a supposed larger atomic outbox transaction. A must
expose a composite method using `_append_thread_message_uncommitted` under
one owned `BEGIN IMMEDIATE` for message, receipt and audit, including rollback
on audit failure. B consumes that method without a second thread writer.

Quota includes every physical shared byte: active, expired/protected,
recoverable, legacy and uncertain bytes, plus in-flight reservations. Count a
byte once if states overlap; tombstones with no backing bytes count zero.
Unknown or partial inventory refuses uploads, not downloads/listing. Per-org
admission must reconcile DB counters with no-follow filesystem size/hash
observations; an external writer or unexplained file invalidates admission
until reconciled. Old writers cannot be allowed during enforced operation.

Reject `used + reserved + upload_size > 5,000,000,000` with
`413 artifact_quota_exceeded`, reporting used/reserved/requested/quota bytes
and advice to renew/retire/classify through supported workflows. An upload that
exactly fills the quota is allowed; once usage is at the limit every positive
upload is refused, including overwrites. Below the limit, replacement reserves
its entire incoming size until old bytes are replaced, so temporary bytes
cannot evade quota. Refusal leaves original bytes/metadata intact. There is no
automatic eviction, early hard deletion, grandfathered quota discount or quota
increase surface. Renew/restore of already-counted bytes and GET/list remain
available when full.

On crossing from below 80% to at/above it, emit one `artifact_quota_alert` and
one durable message in the org storage-management thread, addressed to the
resolved owning/responsible manager and visible to Founder. Initialize the
latch from first reconciled usage: already above 80% sends one initial alert.
Re-arm only after verified usage drops below 80%; repeated daily scans/uploads
above it do not spam. Crossing sequence plus org is the idempotency key.
Unlinking bytes, not merely marking recoverable, reduces usage. Full-capacity
refusals are audited per request; they do not generate repeated inbox alerts.

## Daily scheduler and preview rollout

Add an org-artifact lifecycle scheduler, separate from workspace cleanup's
agent-task producer and the machine disk sweeper's filesystem deletion rules.
Reuse their lifecycle/cancellation patterns, not their eligibility authority.
Proposed schedule: daily **04:00 in the org's configured timezone**, with a
30-second startup warm-up, current-window-only catch-up, fold=0 on repeated
wall times and skip nonexistent occurrences. Persist one run/window identity;
restarts resume only journaled unfinished operations after reconciliation, not
an unaudited replay of destructive work. No full historical catch-up.

Use keyset batches of 250 metadata rows and a single 60-second monotonic work
budget per org pass, including reference and directory scans. Save an honest
incomplete/cursor outcome; unseen work is held until a later daily pass.
Per-operation I/O and SQL are bounded within that same budget. A proof that
does not fit cannot authorize deletion. Budget values are proposed operational
bounds, not changes to Founder-reserved retention/quota numbers. Required
implementation evidence must include the roughly 7,500-file existing shape;
if a complete reference check cannot finish, optimize/index the actual
recognized reference projection under writer transactions rather than treating
cap exhaustion as no dependency.

Org configuration proposal:

```yaml
shared_artifacts:
  cleanup_mode: preview  # default; allowed values preview, enforce, off
```

Unknown/unreadable mode holds mutation. `off` suspends cleanup while metadata,
quota and downloads still work. All three modes keep audited owner notices and
quota controls. Preview plans expiry/deletion/restoration eligibility and emits
`artifact_preview`; the preview sweep changes no artifact state/expiry or
backing bytes, does not consume a recovery interval, and manual DELETE also
returns a preview without retirement. Ordinary uploads and explicit user
renew/restore remain available; their real effects are audited as such.

Persist the first successful complete preview time. The first seven elapsed
days are mandatory preview; `enforce` configured sooner is still effective
preview with an audited refusal reason. engineering_manager reviews the
complete preview receipts and inventory; **only an explicitly authorized
operator**, on engineering_manager's recorded acceptance, flips the config
through the supported config workflow during normal release practice. Agents
do not flip it by editing the host file. No automatic time-only enablement.
Missing review, unresolved STOP items or unknown ownership/custody keeps
destructive activation held. Legacy per-item classification additionally needs
the reviewed inventory below. Returning to preview/off stops admission of
new destructive operations; an already issued unlink is an uncertain effect
until its journal reconciles, not reversible by changing a flag.

## Mutation ordering, recovery and audit records

SQLite COMMIT and filesystem rename/unlink are not one atomic operation. Use
the durable journal rather than claiming a rollback can resurrect deleted
bytes. Validate metadata, capacity, references and current identity first;
persist the operation intent and mandatory audit before any destructive effect.
For state-only expiry/renewal/restore, state + audit + receipts commit in one
owned transaction. For puts/replacements, reserve and stage this request's
bytes, commit the intended generation/operation, atomically publish bytes,
then finalize the row and `artifact_put`; do not report success before final
commit. Incomplete generation publication is unavailable and reconcilable,
never an apparently active metadata-only upload. Refuse generic store access
to pending journal objects. Compensation may unlink only positively identified
request-owned staging, and stays armed through COMMIT.

For deletion: persist `artifact_delete_intent` with verified identity and
expected revision, recheck the dependency/identity fence, unlink, then commit
`deleted` plus `artifact_delete` and actual reclaimed size. If final audit/
commit fails, retain the intent and uncertain operation; GET explains pending
cleanup instead of falling through to 404. On restart, reconcile the exact
intent/identity under exclusive daemon ownership. Positively missing bytes can
complete an already-issued operation with a recovery audit; a different file,
unavailable path or uncertain writer is held and never re-unlinked. An audit
failure before intent persistence means no unlink. Preview and notice audit
failure similarly cannot be recorded as successful delivery/run completion.

All events append through the existing audit writer with unchanged columns:
`id` allocated by SQLite, `timestamp` actual UTC, `agent` caller's permanent
ID for manual actions or **`daemon` for SYSTEM cleanup**, `action` as below,
and JSON `payload`. SYSTEM is an operation source, not a newly invented agent.
Common artifact payload: `name`, `generation`, `operation_id`, `source`
(`manual`/`system`), `owner_agent_id`, `origin_task_id`, `origin_thread_id`,
before/after state, reason, size, expiry, effective mode and outcome. Do not log
file contents, secrets, local source paths or invented historical provenance.

| Action | `audit_log.task_id` | Additional payload / when |
| --- | --- | --- |
| `artifact_put` | `artifact:<name>` | Preserve existing `name,size_bytes` keys; add metadata/generation. Only successful finalized publication. |
| `artifact_renew` | `artifact:<name>` | Continuing use, previous/new expiry, retention/reason; committed renewal. |
| `artifact_preview` | `artifact:<name>` | Would-expire/would-delete/would-protect or restore eligibility, exact evidence/decision, `mutated=false`; once per candidate/run. |
| `artifact_expire` | `artifact:<name>` | Actual transition, expired_on, recovery start/deadline. |
| `artifact_restore` | `artifact:<name>` | Actor, continuing use, verified hash, previous deadline and new expiry. |
| `artifact_delete_intent` / `artifact_delete` | `artifact:<name>` | Intent before unlink / final actual deletion; keep existing delete `name` key and add reclaimed bytes. Never log final success merely for a planned unlink. |
| `artifact_protected` | `artifact:<name>` | Verified TASK/THR/generation references or explicit unavailable scope; emit per run decision, without raw message bodies. |
| `artifact_notice` | `artifact:<name>` | Expiry notice idempotency key, destination/message seq, scheduled/actual time, delivered/late/failed outcome. |
| `artifact_classify` | `artifact:<name>` | Legacy before/after, inventory digest, review citation, owner decision and imported expiry/reason. |
| `artifact_operation_failed` / `artifact_operation_reconciled` | `artifact:<name>` | Phase, safe error code, original operation identity and known/unknown effects. No rewriting earlier receipts. |
| `artifact_quota_check` / `artifact_quota_refused` / `artifact_quota_alert` | `config:shared_artifacts` | Usage/reservation/quota bytes, crossing sequence/latch, request name, caller and result; alert destination/receipt. Check events for each admission and daily reconcile. |
| `artifact_sweep_started` / `artifact_sweep_finished` | `config:shared_artifacts` | Run/window ID, preview/enforce/off, complete/incomplete/failed scopes, counters, measured bytes, cutoff and deadline exhaustion. |
| `org_config_write` | `config:shared_artifacts` | Existing config-write shape; actor, before/after mode, preview acceptance citation. |

For artifact events the originating TASK/THR IDs live **only in payload** and
metadata, never replace `artifact:<name>`. An artifact literally named
`TASK-123` still has scope `artifact:TASK-123`. Notice destination THR ID also
lives in payload. A thread lifecycle event emitted by existing thread machinery
keeps its own THR scope, separately. Org-wide actions reuse the established
`config:` scope convention; no new prefix meaning or audit-log migration.
Dedup receipts/state and their associated audits must share the transaction;
an audit row alone is not delivery, byte-reclamation or activation proof.

## Legacy inventory and classification

At day one, register discovered pre-change blobs as `legacy_unclassified` with
observed name/size/hash and null owner/origin/purpose/expiry where unknown.
Preserve mtime as an observation, never as upload time or deletion consent.
All approximately **1.7 GB** of legacy bytes count immediately. On the stated
snapshot this is below 4 GB warning / 5 GB limit; use actual reconciled bytes,
not the snapshot, for admission. If actual usage is already above the warning
or limit, send the initial alert/refuse new uploads accordingly; do not delete
or exclude legacy bytes to manufacture headroom. Explain that classification
does not itself reclaim space.

The sibling read-only inventory produces an immutable manifest with exact name,
hash, size, dependency evidence, proposed owning agent/manager, purpose,
classification and reviewed expiry. Unknown owners go to the responsible
manager; unresolved routing goes to the org storage-management thread/Founder.
No extension, prefix, age or closed-task link alone establishes deletion safety.

Proposed operator import path, shipped only in slice D:

```text
python -m runtime.daemon.artifact_legacy_import --org ORG \
  --inventory /absolute/reviewed-inventory.json \
  --review /absolute/engineering-manager-review.json --dry-run
```

An applying invocation additionally requires `--apply` and the exact approved
manifest/review digests. It runs under existing daemon/operator coordination,
not a second ad hoc SQLite writer beside the live daemon. The implementation
must document its supported invocation before use; this proposed command does
not authorize a live import. It validates every item against current bytes and
references, presents an exact preview and records the authenticated manager
review citation. A self-written reviewer string or filename is not acceptance.
Changed/missing/unknown items stay unclassified. No wildcard deletion decisions.

Accepted classifications are default/reference with full metadata and an
explicit bounded future expiry measured from classification, or a reviewed
retire decision entering the seven-day recoverable path after preview/enforce
gates and live-reference checks. Leave undecided items legacy_unclassified.
The inventory import never physically deletes bytes. Per-item review provenance
remains required for subsequent legacy expiry/delete even after the org enables
enforcement. No unaudited backfill from artifact_put audit attribution, no
automatic 90-day assignment and no bulk age-based retirement. Canonical skill
sources remain unresolved under STOP item 3 until their compatibility plan is
accepted; this is not approval for indefinite exempt storage.

## Historical retrieval and errors

`GET /artifacts/{name}` first resolves metadata/generation, then bytes. For
expired, recoverable or deleted content return **410** with a structured detail:

```json
{
  "code": "artifact_unavailable",
  "name": "example.zip",
  "generation": 1,
  "state": "recoverable",
  "expired_on": "2027-02-21T00:00:00Z",
  "owner": "dev_agent",
  "reason": "retention_expired",
  "recoverable_until": "2027-02-28T04:00:00Z",
  "can_restore": true
}
```

Include actual deletion time for deleted rows; `can_restore=false` after the
deadline. Unknown owner is explicit null/unknown with manager routing, never a
fabricated owner. Known missing/mismatched bytes return a structured unavailable
response with state/reason `storage_missing`/`reconciliation_pending`, not bare
404 or a success claim. Only a name never recorded and absent from a complete
reconciliation gets `404 artifact_not_found`. Protected active/expired files
with positively live dependencies remain downloadable with expiry/protection
metadata headers; GET never renews them.

CLI get prints, for example, `example.zip expired on 2027-02-21; owner:
dev_agent; recoverable until 2027-02-28 (retention_expired). Use artifacts
restore ...`, exits nonzero and leaves the output file unchanged. Tombstones
appear in list with state/owner/expiry, with a filter to show available-only;
GET/list continue at quota and during cleanup suspension.

Thread history retains attachment records and the original URL. New thread API
attachment projections include state/expiry/owner/reason and generation URL;
CLI `threads show` uses those fields for readable unavailable labels. Clicking
an old URL receives the same explanatory 410 JSON. Do not require a renderer
change in `web/` to make error bodies useful; richer inline browser treatment
would require sibling coordination and is not assigned here. No history
rewriting, broken link erasure or hidden redirect to different content.

## Writer inventory and rerouting

Search at the baseline included `runtime/`, `cli/`, `runtime/skills/bundled/`,
`docs/agent-guides/`, and `CLAUDE.md` for `artifacts put`, `put_artifact(`,
`ArtifactStore(`, `artifacts_dir`, `/artifacts/`, and Shared Artifacts wording,
then inspected screenshot/log/evidence matches. `protocol/skills/` does **not
exist** at this baseline (`protocol/` has no skill writers). The deployed
generated agent instructions shown in the task match the generator below;
they are not canonical files to edit by hand.

| Writer/instruction or consumer found | Disposition |
| --- | --- |
| `runtime/orchestrator/workspace_adapters.py::_shared_artifacts_section` (around 1483) | Concrete routine-evidence writer instruction: recommends screenshots and a QA screenshot example, plus metadata-free put. Replace those examples with `output/<actual-task-id>/` plus output_dir/recall and authorized fresh task attachment for binary handoff. Retain metadata-complete put only for persistent downloadable deliverables. Bootstrap regeneration uses supported enrollment/update/release paths, never edits managed agent instructions directly. |
| `runtime/skills/bundled/thread/SKILL.md` shared-artifact escape hatch (around 201) | Generic pre-upload recipe can route routine evidence to shared storage. Limit it to persistent shared deliverables with required metadata; routine logs/screenshots stay task output with supported task handoff. Explain the derived metadata for explicit `--shared`. |
| `cli/commands/threads.py::_merge_uploaded_attachments` and `cmd_threads_compose` | Actual automatic upload when `--shared` is explicit; no screenshot/log classifier exists. Keep explicit persistent sharing with derived metadata. Instructions must stop selecting this path for routine evidence; do not silently move a user-requested shared file to private storage. Resolve compose/Founder STOP first. |
| `cli/commands/artifacts.py::cmd_artifacts_put`, `cli/client/client.py::put_artifact`, `runtime/daemon/routes/artifacts.py::put_artifact` | Generic upload chain, not a screenshot generator. Enforce complete metadata/quota and expose lifecycle errors; examples point routine evidence to task output. |
| `runtime/daemon/routes/custom_skills.py::_write_artifact` and compensation helpers | Actual automatic writer of canonical skill source, not routine logs/screenshots. **Do not reroute to task output.** STOP item 3 requires a separate compatible source-storage decision. Preserve INSERT/write/COMMIT compensation ordering. |
| `runtime/infrastructure/artifact_store.py::put` | Generic blob writer; keep existing validation/atomic replace. Org shared service owns policy. Direct custom-skill callers cannot become an uncounted metadata bypass. |
| `runtime/skills/bundled/start-task/SKILL.md` task-output hygiene and attachment ownership guidance | Already routes task evidence to output and prohibits Shared Artifacts ownership workarounds. Preserve that policy and add explicit retention-aware persistent-file pointer; do not invent an upload requirement. |
| `runtime/skills/bundled/manage-agent/SKILL.md` | No artifact upload instruction found. Its bootstrap-regeneration section must point to the regenerated artifact policy; no roster/permission changes. |
| `runtime/orchestrator/run_step.py` completion guidance | Already says keep task-only evidence in output_dir/recall. Preserve; no routine shared writer found. |
| `docs/agent-guides/features-and-invariants.md` Shared Artifacts; mirrored attachment guidance in `orchestrator-contracts.md`, `runtime-and-configuration.md`, and `contracts/task-orchestration.md`; `CLAUDE.md`/`AGENTS.md` attachment rule | Existing prohibition is correct. Update API/retention statements and examples; routine evidence remains task output. The old page description is reconciled by the sibling removal, not copied into new instructions. |
| `runtime/daemon/app.py`, `routes/orgs.py`, `runtime/orchestrator/_paths.py` | Directory setup/path consumers, not routine file writers. No rerouting. |
| `runtime/daemon/routes/threads.py` attachment reader; `workspace_adapters.py` custom-skill materialization; `routes/skills.py` recovery; `db/schema.py::_retire_skill_lifecycle_if_present` | Readers or separately scoped deletion consumers. Integrate lifecycle lookup for thread reads; preserve the canonical-source/retirement invariants pending STOP disposition. |

No automatic routine screenshot/log *script* was found in the assigned search
roots beyond these instructions and explicit upload paths. This is not proof
about external custom tools, historical task scripts or `web/`/`e2e/` writers;
the sibling inventory must reconcile any additional deployed writers before
rollout. Do not rename external outputs or claim they were fixed in this PR.
Task output follows its existing size/retention contract; this change does not
create a second indefinite evidence archive.

## Implementation PR slices and doc parity

Four ordered slices below have disjoint file ownership. A exposes the service
interfaces B and D consume; B and D do not patch A's schema/routes. Tables,
outbox and lifecycle transition methods needed later are installed in A with
destructive scheduling absent. Inventory review and STOP resolutions precede
dependent implementation. If the accepted solution requires overlapping files,
manager must reslice before dispatch rather than promise disjointness falsely.

| Order / slice | Exclusive implementation files and responsibility | Same-PR documentation |
| --- | --- | --- |
| A — metadata and retrieval/admission seam | `runtime/infrastructure/db/schema.py`; new `runtime/infrastructure/db/shared_artifacts.py`, `runtime/infrastructure/shared_artifact_service.py`; `runtime/infrastructure/database.py` mixin wiring; `runtime/infrastructure/artifact_store.py`; `runtime/infrastructure/audit_logger.py`; `runtime/daemon/routes/artifacts.py`, `runtime/daemon/routes/threads.py`; `runtime/infrastructure/db/threads.py`; `cli/commands/artifacts.py`, `cli/commands/threads.py`, `cli/client/client.py`; `tests/contract/openapi.json`, `tests/contract/route-classification.json` if required. Full metadata, quota admission, journal/transition interfaces, renew/restore, exact overwrite generations, explainable GET/thread projections and audit helpers. No sweeper activation. | `docs/agent-guides/features-and-invariants.md` “Shared Artifacts”, “Shared artifacts” feature entry and thread attachment contract; `docs/agent-guides/web-and-cli.md` artifact CLI/API/error reference; new `docs/agent-guides/contracts/shared-artifacts.md` owns the final contract and imports/transition interfaces. |
| B — daily preview, reference proof and notices | New `runtime/daemon/shared_artifacts_scheduler.py`, `runtime/daemon/shared_artifacts_notices.py`, `runtime/infrastructure/shared_artifact_references.py`; `runtime/daemon/app.py` lifespan wiring; `runtime/orchestrator/org_config.py` flag parsing/validation. Uses A's transactional public service methods and audit helpers; no second metadata owner or edits to existing disk/workspace schedulers. Quota alert/dedup delivery lives here; admission enforcement already ships in A. | `docs/agent-guides/runtime-and-configuration.md` shared-artifact mode, timing, rollback and rollout gates; `docs/agent-guides/contracts/jobs-and-cleanup.md` distinguish artifact sweep authority from workspace/disk cleanup; `docs/agent-guides/contracts/README.md` link the contract. |
| C — generated writers and operating instructions | `runtime/orchestrator/workspace_adapters.py::_shared_artifacts_section`; `runtime/skills/bundled/start-task/SKILL.md`, `runtime/skills/bundled/thread/SKILL.md`, `runtime/skills/bundled/manage-agent/SKILL.md`. No canonical-source storage edits without separately resolved scope. | Canonical `AGENTS.md` (“Task model rules” attachment ownership; “Agent-side rules” task-only evidence; “Feature pointers” shared-file contract pointer), exposed through the `CLAUDE.md` symlink. `docs/agent-guides/task-output-practice.md` binary handoff/routine screenshots/logs; `docs/agent-guides/orchestrator-contracts.md` attachment ownership mirror; `docs/agent-guides/project-layout.md` artifact metadata/service ownership. product_lead publishes KB `shared-artifacts-hygiene` mirror: persistent-only test, owner-prunes-own SYSTEM exception, all numbers, notices, recovery, legacy review and writer rerouting. |
| D — reviewed legacy classification | New `runtime/daemon/artifact_legacy_import.py`, `runtime/infrastructure/shared_artifact_inventory.py`; uses A's already installed review/metadata APIs and B's preview decisions. No schema or existing CLI edits. Only after engineering_manager accepts the sibling exact inventory; no import-side unlink. | New `docs/operations/shared-artifacts-classification.md`: reviewed manifest/review schemas, dry-run/apply invocation, exact-hash failure behavior and day-one quota accounting. |

Any future tests use separate slice-owned supported files and the
test-authoring-evidence-gate. No tests are added in this spec PR. The OpenAPI
snapshot/classification files in A are contract data, not permission to alter
web clients; a new route requiring TS coverage needs explicit sibling ownership
or an already legitimate CLI-only classification. Do not claim a classification
exemption merely to evade a required contract.

The base has no `CLAUDE.md` “Shared Artifacts” or “Load-bearing invariants”
section to edit: the exact maintained owner is the linked features guide above.
Update canonical `AGENTS.md` pointers only, never replace its symlink. B's
configuration guide and C's instruction mirrors must preserve A's contract by
linking rather than duplicating a divergent policy. Bundled workspace-cleanup
and jobs skills contain no routine shared upload recipe; preserve their
protected-root/authority wording rather than grant artifact deletion there.
`protocol/skills/` needs no edit because it is absent. No edits to materialized
workspace `.agents/skills` or `.claude/skills` are permitted.

Product owns final KB wording and the SYSTEM exception. Until Product's
THR-394 disposition is recorded, carry the exact draft below for review; no
claim that this PR updated the live KB. Unknown-owner routing, notice surface,
restore access interpretation and skill-source disposition need manager/Product
acceptance before related behavior ships.

## Verification and implementation acceptance

This docs PR runs the repository's supported lint/static lane
`scripts/local_ci.sh all`, guard verification, `git diff --check`, and a full
base-to-head scope read. No runtime behavior is claimed from those results.
Required hosted checks remain exactly Source Boundary Lint and Web Lint
(Node 24); no new tests/builds/E2E triggers. Python units remain retired,
general integration stays SKIPPED under THR-243, and this task runs no Product
E2E. Normal hooks, independent review and manager-controlled merge remain.

Before implementation, manager accepts finite cases in supported isolated
venues; this table is a case proposal, not executed evidence or permission to
run the retired suites:

| Requirement / slice | Scenario and observable assertion | Level / isolation |
| --- | --- | --- |
| Metadata / A | Missing each required field, both/neither origin, foreign origin, blank reason and bad expiry reject 4xx with zero new bytes/rows/reservation; valid default/reference return exact 90/180 bound. | CLI/ASGI + private SQLite/filesystem, no live org or lifespan. |
| Overwrite/crash / A | Exact active replacement and generation history; case-alias/live-ref/legacy/tombstone refusals preserve old hash. Fail staging, rename, audit and COMMIT; reconcile only the exact journaled effect, never a peer file. | Real service/storage, private disposable dirs and controlled failure points. |
| Renew/restore / A | GET never renews; exact renewal maximum accepted, +1 instant refused; recoverable restore before boundary works, after boundary/deleted refuses; quota full still allows counted-byte restore. | CLI/API with controlled UTC clock and private data. |
| Tombstones / A | Original archived-thread URL/API/CLI get of expired/recoverable/deleted returns date/owner/reason, nonzero CLI with unchanged destination; never-known name remains 404. | Real retrieval and thread projection seam, no web/E2E change. |
| Quota / A+B | Active+recoverable+protected+legacy+reservations sum once; exact limit/+1, overwrite staging and concurrent uploads cannot oversubscribe; crossing→repeat→below→recross yields exactly two alerts across restart. | Concurrent service admission and real receipt/message transactions on isolated org. |
| References / B | Pending/queued/delegated task, open shared attachment and recognized text link hold expiry/delete; archived-only link does not. Malformed/unavailable/capped scan holds, never empty. Reference arrival at final fence cannot race unlink. Canonical-skill dependency blocks activation. | Real DB readers/writers and filesystem, bounded concurrency barriers. |
| Sweep / B | Expiry→full seven-day recovery→actual unlink+tombstone; live hold removal and new recovery semantics; inode/hash/symlink replacement holds; interruption after unlink retains explainable journal. | Controlled-clock direct service/scheduler pass, private storage. |
| Preview/notices / B | First week, premature enforce, off/invalid config and incomplete/restarted passes never mutate bytes; one owner notice per expiry at minus seven days, late delivery truthful; missing route/audit fails without false delivery. | Direct scheduler/notice service, synthetic roster and thread inbox rows. |
| Legacy / D | New daemon/old dir, old writer/new rows, 1.7 GB day-one accounting, unknown owner, changed inventory hash, missing manager review and reviewed retire decision; no inferred expiry and no import-side unlink. | Private old/new schema/directories plus real import validation. |
| Instructions / C | Generated prompt and each maintained example routes routine evidence to task output and reserves shared put for complete persistent metadata; new bootstrap includes it without editing managed files. | Targeted source/generated-output inspection; no mirror-only test. |

State unsupported proof as NOT RUN, never PASS. The reviewed inventory,
seven-day preview and exact-source compatibility/activation evidence are
future release gates, not satisfied by a design review or a green lint run.

## Appendix: Proposed product wording

Verbatim draft additions from THR-394 seq1, copied for product_lead review.
These remain draft wording, not a live KB update or a waiver of the STOP items.

**Draft additions**
1. Every new shared file carries owning agent, originating task or thread, purpose and expiry. Uploads without them are refused.
2. Default expiry 90 days. Design sources/reference files may have up to 180 days with a named owner and reason. Owners may renew with a stated continuing use and a new expiry (same maximum). Downloads never renew. No permanent exemption.
3. The owner is notified 7 days before expiry. Expired files are recoverable for 7 days, then their bytes are deleted. References to expired/deleted files explain the expiry (date, owner, reason).
4. Quota 5 GB per org; alert at 80%; new uploads refused at the limit; downloads keep working. Recoverable files and live-task dependencies count.
5. **Owner-prunes-own amendment:** agents still prune only their own files, except a narrowly scoped, audited SYSTEM cleanup may expire, recover and delete files under the rules above. It protects verified live-task dependencies (running/queued tasks, open thread links). Existing pre-change files are not expired until an inventory/preview is reviewed; unknown-owner files go to the responsible manager. The first week is preview-only.
6. Routine screenshots/logs go to task output, never Shared Artifacts.
