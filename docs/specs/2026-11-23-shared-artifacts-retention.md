# Shared Artifacts ownership, retention, quota and audited system cleanup

Status: **Founder-approved; enforcement pending verified rollout**. This is a
design for engineering_manager review, **not implementation or activation
authority**. TASK-18710, revised by TASK-18728; parent TASK-18707, root
TASK-18703. Founder approval: THR-392 seq13/14, incorporating Product's seq12.
Product accepted the contract wording in THR-394 seq2; engineering_manager
accepted it and updated KB `shared-artifacts-hygiene` in seq3. Product acceptance
is complete; this PR does not establish deployed enforcement.

Source baseline: freshly fetched `origin/main`
`47709e5e718047fe4ae74e187caeec918d7af274`. This PR adds this specification
only. No executable code, migrations, tests, builds, E2E triggers, deployment,
restart, cleanup or configuration changes. `web/` and `e2e/` belong to the
sibling page-removal slice and are excluded here.

## Assumptions & decided questions

The following record the manager's TASK-18728 dispositions, not observations of
shipped enforcement. Implementation waits for manager acceptance of the revised
design. Only the remaining **STOP** below holds destructive activation; it does
not prevent design or implementation dispatch. Operational scheduling/budget
values below remain explicit proposals. No new Founder decision is requested.

1. Retention uses UTC instants and elapsed 24-hour days: default 90 days;
   reference/design source at most 180 days. **Q1 decided:** 5 GB is
   **5,000,000,000 bytes**, warning at **4,000,000,000 bytes**. Only Founder can
   change the approved capacity or percentages. There is no permanent exemption.
2. Existing org bearer authentication and org isolation remain unchanged.
   `agent` currently supplies audit attribution, not authenticated per-agent
   ownership. **Q2 decided:** renew/restore use the existing org-bearer access:
   any caller already authorized to use that org's file API may act, recording
   its named actor and continuing use. Operational responsibility belongs to
   the owner, its manager, or Founder. Owner-only enforcement is out of scope and
   must not be implemented here; an agent string is never identity proof.
3. **Q3 decided — verified canonical custom-skill dependency:**
   `runtime/daemon/routes/custom_skills.py::_write_artifact` writes immutable
   B2 version sources into this same directory. Workspace materialization in
   `runtime/orchestrator/workspace_adapters.py::_build_custom_skill_canonical_specs`
   and `runtime/daemon/routes/skills.py` recovery read those keys. Expiring them
   solely because no task/thread link is live can break documented skill
   recovery/materialization. The retention sweep never deletes a source blob
   while a non-retired
   version **or any materialization/recovery reader** references it. This is a
   verified live dependency: counted toward quota, audited as a deferral and
   rechecked every sweep, not a permanent retention class or prefix exemption.
   A completed projection's exact `Q3:reader_exclusion_unavailable` deferral
   is accounted protection, not incomplete sweep scope. It does not prevent a
   complete preview from starting `rollout_started_at`; source protection and
   destructive activation gates remain unchanged.
   Slice B implements/verifies that reference check. **STOP destructive
   activation until it is implemented and verified.** This disposition lifts
   the prior STOP for design and implementation dispatch only. Follow-up:
   engineering_manager owns relocating canonical skill sources to a dedicated
   store in a separate engineering PR; no move is part of these slices.
4. **Q4 decided — server-side bind-at-attach:** CLI `compose --shared`
   currently uploads before a thread ID is allocated, with `thread_id=None`.
   Founder compose passes `agent="founder"`, but Founder is a human, not an
   owning agent. Slice A supplies one server-side bind-at-attach transaction for
   shared followup and compose, including allocation of the real THR ID during
   creation. Founder uploads have `owner_kind=human`, `owner=founder`; the
   accountable agent is the thread's responsible manager at binding, otherwise
   org storage-responsible manager `engineering_manager`. Metadata-free founder
   browser uploads are provisional `pending-attribution` only, as detailed
   below. Agent/CLI puts still require metadata. Do not invent a Founder agent,
   infer origin from names, publish an empty thread first or use a draft ID.
5. **Q5 decided:** exact-name overwrite preserves today's active-file compatibility;
   expired generations remain explainable through optional generation links.
   The generation/tombstone/name-reuse policy below is accepted.
6. **Q6 decided:** notice destination is a durable system-message thread per owning
   agent, plus one org storage-management thread visible to the responsible
   manager and Founder. These reuse the existing Threads inbox, not a new
   notification product. Missing/retired owner or unresolved manager falls back
   to the responsible manager via the org storage-management thread, recording
   the routing reason. The full-JSON encoding below resolves the rendering gate.
7. **Q7 decided:** engineering_manager review of sibling inventory TASK-18706
   **and** seven days of preview are both required before any legacy
   classification or destructive activation. Preview time alone is insufficient.
   No fresh live inventory was made in this revision. Approximately 1.7 GB is the
   THR-392 snapshot, not a measured day-one total. The sibling reviewed inventory
   supplies exact names, hashes, sizes, owners and decisions. The import and
   destructive switch remain gated on both requirements; a docs PR cannot
   satisfy them.

Explicit compatibility assumptions: existing bearer authority is shared, not a
new per-agent authentication mechanism (`runtime/daemon/auth.py::require_token`
and `_require_human`). The provisional exception uses existing founder bearer
authority with founder attribution; it does not claim that an `agent` query
string authenticates a human. The browser follow-up and slice A must retain
that boundary. Notices use the existing tagless JSON rendering path, not a
new-kind body fallback; visibility remains an implementation acceptance case.

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
| `shared_artifact_generations` | Composite `(name, generation)` key; `owner_kind` (agent/human), `owner` (permanent agent ID or founder), accountable `owner_agent_id`, originating task ID or thread ID (exactly one for attributed rows), purpose, retention class, reference reason, created/renewed/expiry UTC, byte size and SHA-256, state, expiry/recovery/deletion timestamps, deletion/hold reason, classification review ID. Legacy may have null metadata. Provisional human rows may have null origin/purpose until binding, and null expiry only during their first 24 hours; persist attribution status, binding time and timeout-routing receipt. |
| `shared_artifact_operations` | Unique operation ID, name/generation, expected revision/hash, operation/phase, reserved bytes, intended before/after state, filesystem identity, timestamps and failure/uncertainty. Durable journal bridges SQLite and filesystem effects. |
| `shared_artifact_notice_receipts` | Unique `(name, generation, expires_at, notice_kind)`; destination thread/message sequence, delivery state, outbox ID. Renewal to a different expiry creates a new notice identity. |
| `shared_artifact_org_state` | Singleton quota crossing latch/sequence, usage reconciliation status, rollout start UTC, last completed daily window and preview acceptance citation. No configurable override of Founder-reserved numbers. |
| `shared_artifact_inventory_reviews` | Immutable reviewed inventory digest, reviewer permanent ID/task/ruling citation and approved exact item decisions. Legacy classification cites this review. |

`active`, `recoverable`, `deleted`, `pending-attribution`, and
`legacy_unclassified` are persisted states. `pending-attribution` is a bounded
new-upload state, never legacy classification. `expired` is an unavailable read
projection only after an owned transaction starts recovery; a GET that first
detects expiry must use the same mode/reference/transition gates as the sweep and
persist the actual unavailability/recovery start before returning expiry 410.
It cannot start an unrecorded unavailable interval before the sweep.
`protected` is a reasoned overlay on
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
| `POST /artifacts` | Keep multipart file and query-style metadata, adding the same owner/origin/purpose/retention/reason/optional expiry fields. Agent/CLI puts reject missing metadata with `422 artifact_metadata_required` and `missing_fields`; reject bad origin/class/expiry with `422 artifact_metadata_invalid` and field reasons. The sole metadata-free compatibility case is a founder-bearer upload attributed to founder: accept only as `pending-attribution`, using the bind/24-hour rules below. Partial or invalid supplied metadata is not a provisional fallback. No filename/mtime defaults for ownership or origin. |
| `artifacts renew NAME --agent ID --continuing-use TEXT --expires-at UTC [--retention CLASS --reason TEXT]` / `POST /artifacts/{name:path}/renew` | JSON API body carries the same fields. Expiry must be strictly after server renewal time and no later than renewal time + 90 days (default) or + 180 days (reference). Reference still requires named owner/reason. Keep original provenance; save continuing use and renewal history. Downloads/listing never change expiry. |
| `artifacts restore NAME --agent ID --continuing-use TEXT --expires-at UTC` / `POST /artifacts/{name:path}/restore` | Same retention limits, reason rules and bearer access as renewal. Restore only while recoverable and before `recoverable_until`; verify intact bytes, then atomically renew to active. No quota credit is consumed twice: recovery bytes already count. Deleted bytes cannot be restored. |

API registration must place specific lifecycle routes before any conflicting
catch-all name route and preserve names with nested path segments. Missing
fields should have a stable error code as well as readable text, including
model-validation errors. Unknown supplied origin/owner rejects before mutation;
origin validation is provenance validation, not a new access grant.

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

Existing-thread agent `send/reply --shared`: derive owner from the composer
permanent agent ID, origin from the real thread ID, purpose exactly `thread
attachment`, retention default and expiry upload time + 90 days. The CLI supplies
complete metadata and the daemon validates it against the message context under
the existing bearer/attribution contract. Explicit reference-class retention
requires an ordinary metadata-complete put, then attachment by name/generation.
Ordinary private
thread-scoped attachments keep their separate storage/access contract.

**Slice A implements the provisional upload and server-side bind-at-attach
flow.** The retained browser writer at
`web/src/features/threads/ThreadsPage.tsx:808-812` calls
`web/src/lib/api/artifacts.ts::uploadArtifact` with only file/name/agent=founder.
Its existing founder-bearer, metadata-free upload remains accepted, but **only**
as a provisional `pending-attribution` generation. Set `owner_kind=human`,
`owner=founder`, accountable `owner_agent_id=engineering_manager`, upload UTC,
hash/size and default retention. Origin and purpose remain explicitly unbound;
never infer a THR ID from the generated name. Quota, journaling and audit apply
immediately. This is not an agent/CLI metadata exemption, and an incomplete
agent upload still fails 422. The shared bearer and actor attribution stay as
they are; this compatibility flow grants no new authentication authority.

When followup/compose first attaches that generation, **one server-owned
transaction** verifies bytes/generation and attribution state, creates the real
thread ID if composing, binds `origin_thread_id` to that THR ID and
`purpose='thread attachment'`, sets `expires_at=bound_at + 90 days`, resolves the
thread's responsible manager as accountable agent (otherwise
`engineering_manager`), transitions to active, and commits
message/attachment/binding/audit together.
It retains human ownership. Replaying an attachment never restarts expiry;
subsequent links retain the first bound provenance. Concurrent bind attempts
serialize by generation/revision. Rollback leaves the provisional record unbound
and publishes no partial thread, message or metadata. This same transaction is
the Q4 solution for human pre-ID compose and shared attachment binding.

At upload + 24 hours, an unbound provisional record gets
`expires_at=uploaded_at + 90 days` and a durable notice routed to the responsible
manager (`engineering_manager` while no thread is known). Persist that timeout
decision/receipt and recheck through the service on reads/attaches and daily
sweeps; a delayed sweep does not move the 24-hour deadline or expiry forward.
Unbound provenance stays explicit, not fabricated or silently legacy. It then
participates in normal notice/expiry/recovery, quota and dependency checks.
Its first valid bind while still available may start the specified bind-time
grant; an expired/recoverable upload needs explicit restore with continuing use
and a bounded expiry before attachment. No silent restore or repeated bind grant.

Agent/CLI `artifacts put` stays strictly metadata-required. CLI shared compose
must stop pre-uploading with `thread_id=None`; slice A uses a server-side shared
multipart compose request that derives full agent metadata after allocating the
real THR ID and uses the same bind transaction. Human CLI compose uses that
transaction's human-owner/manager mapping, not metadata-free CLI puts. No blob
from that composite request becomes public before its thread/origin commits.
Failed thread/attachment creation compensates
only this request's newly written bytes, including COMMIT failure. Journal any
uncertain compensation; do not delete a pre-existing or concurrently committed
blob. `_normalize_attachments` must use lifecycle lookup so a filesystem file
alone does not validate an expired attachment. New links pin a generation;
historical attachment rows and messages remain intact.

**Owned web follow-up prerequisite:** after the sibling Shared Artifacts
page-removal slice (TASK-18708 / PR #1836) merges, engineering_manager dispatches
frontend_engineer to update the retained `web/` thread attachment uploader and
API helper to send `thread_id` and `purpose` explicitly. Pre-ID compose still
uses server-side binding. That separate follow-up is required for the explicit
browser-metadata contract; it is not required to invent an origin at upload
time or to preserve the current client through slice A's provisional flow.
This docs-only PR and slices A-D edit no `web/` or `e2e/` files.

## Retention, recovery and verified dependencies

At upload/renewal, `0 < expiry-now <= class maximum`. No class, flag, reason or
download grants perpetual retention. A daily sweep first classifies each
eligible generation using current metadata and verified references:

| Condition | Result |
| --- | --- |
| `legacy_unclassified` or unresolved/mismatched metadata outside the intentional provisional contract | Keep bytes; report classification/uncertainty. Never infer expiry. |
| `pending-attribution`, still unbound | Apply the upload + 24-hour timeout rule above; do not treat the intentional unbound origin as legacy/indeterminate protection forever. Once its upload-based expiry is due, use normal reference/expiry/recovery rules. |
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

The authoritative scan reads the complete bounded live projection of the
same-org SQLite task/thread corpus,
not a filesystem name prefix or a recent-page sample:

1. Live tasks are `pending`, `in_progress` (including delegated/job waits), and
   conservatively `escalated`; queued tasks are included before process launch.
   Parse `tasks.brief` and `tasks.final_output_summary`, and all
   `task_results.output_summary`, `decision_json` string values and
   `risks_flagged` belonging to that task or its direct children (parent-wake
   inputs). Follow explicit same-org TASK-ID tokens in those fields when the task
   exists in the same-org snapshot, to its brief/final summary/result fields,
   up to two handoff hops (`_MAX_HANDOFF_DEPTH = 2`). Live tasks and their
   direct children are depth 0; breadth-first traversal gives each task its
   shortest depth. All text/payloads at depths 0, 1 and 2 are parsed; handoffs
   beyond depth 2 do not load task/result text unless independently in that set.
   Live read-only measurements selected two hops: approximately 17.6 MB /2.6 s,
   near half the unchanged 32 MiB cap, versus 21.7 MB /3.4 s at three hops.
   Unbounded traversal reaches almost all task history, exhausting the byte/time
   budget and letting unrelated historical malformed text poison every proof.
   A valid text mention of a nonexistent task carries no dependency. Malformed or
   undecodable org-qualified identities retain the required handoff. A visited
   set bounds cycles without discarding their already scanned references;
   missing/unreadable required handoffs within the bound make absence
   indeterminate. Malformed selected text/references/payloads and all existing
   status, snapshot, row/byte and deadline failures remain indeterminate. Private
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
delete task results, messages or private attachments. Slice B must additionally
join the canonical B2 version-source keys to every non-retired skill version
and the materialization/recovery readers identified in Q3. A live reader
reference defers expiry and deletion, keeps the source retrievable, counts its
bytes and audits/rechecks the deferral every sweep. Do not infer liveness from
a filename prefix, assume retirement removes every reader, or treat missing/
incomplete reader evidence as a negative. The same fresh-negative fence applies
before unlink. When a completed projection returns exactly
`['Q3:reader_exclusion_unavailable']`, retain protection and quota accounting,
audit/recheck it, and count it separately as `q3_deferred` in
`artifact_sweep_finished`. This decided reader-exclusion hold does not mark the
sweep incomplete. Any other indeterminate reason or reason list, including a
Q3 reason combined with another failure, still marks reference scope incomplete
and prevents the preview clock from starting. Until that exact source check is
implemented and verified,
destructive activation stays STOPped. Unreferenced sources are eligible only
through normal reviewed classification/retention; this adds no permanent source
class. Dedicated-store relocation remains the separate engineering-owned
follow-up, not a condition on design dispatch.

## Notices and quota

Use existing Threads system messages and inbox listing because they are
durable, visible to the owner/Founder and do not need a new notification table
or transport. Persist exact thread IDs in notice receipts, never locate notice
threads solely by their subject. Create/reuse threads with verified system
provenance and owner/accountable-agent participant; Founder remains a human
inbox recipient, not an agent participant. Unknown or retired owners route to
the responsible manager, with org fallback `engineering_manager`; human-owned
files route to their accountable agent and the Founder-visible management
thread. Record both owner and routing, including fallback reason. Do not enqueue
an agent reply merely to read a routine notice.

**Rendering choice (a): use the existing full-JSON encoding.** At the source
baseline, `web/src/features/threads/ThreadsPage.tsx:1740-1746` passes only
`system_payload` to `SystemEvent`; `body_markdown` is not passed.
`SystemEvent:1965` obtains `describeSystem` and renders its entire result at
`:1973-1975` in a wrapping `break-words whitespace-pre-wrap` span. The chip at
`:1938-1939` has no ellipsis/line clamp. `describeSystem:2165` derives
`kind_tag ?? event ?? ''`; its default at **:2232-2233** returns
`tag || JSON.stringify(payload)`. Therefore an unknown **nonempty** kind hides
the other fields, but a payload **omitting both `kind_tag` and `event`** displays
the complete JSON. Do not spoof a task-completion event to carry a notice.

B must write system messages with neither field and all content in a small,
explicit `system_payload`, for example:

```json
{
  "notice_type": "artifact_expiry",
  "file_name": "example.zip",
  "expiry_date": "2027-02-21T00:00:00Z",
  "owner_kind": "agent",
  "owner": "dev_agent",
  "routing": {"agent": "dev_agent", "reason": "owner", "thread_id": "THR-123"},
  "purpose": "design source",
  "recovery_deadline": null,
  "renew_command": "happyranch artifacts renew example.zip --agent dev_agent --continuing-use 'design review' --expires-at 2027-05-01T00:00:00Z",
  "restore_command": "happyranch artifacts restore example.zip --agent dev_agent --continuing-use 'design review' --expires-at 2027-05-01T00:00:00Z"
}
```

At actual expiry include the real recovery deadline, never predict it from the
scheduled expiry. Quota notices use the same tagless encoding, with used/quota
bytes, 80% crossing, responsible manager/routing, and affected files' names,
owners, expiries and applicable commands; split bounded file entries into
deduplicated messages rather than truncate required content. Timeout attribution
notices likewise include the file, upload-based expiry, human owner and manager
route. `notice_type` is ordinary content, not a renderer discriminator.

This also gives the owner agent an already-readable inbox path: the same
participant thread is listed in its Threads inbox and
`happyranch threads show THR-ID` prints `body_markdown` or the **full** system
payload (`cli/commands/threads.py:382-387`). A readable body may be duplicated
for CLI use, but B cannot rely on it for browser visibility. No renderer
prerequisite is needed for this chosen encoding. Slice B must verify the full
file name, expiry, owner, route and renew/restore commands in the actual inbox
rendering as an implementation acceptance case. A committed delivery receipt
proves message persistence only; it is not evidence of visible notice content.

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

Quota includes every physical shared byte: provisional, active, expired/protected,
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

Preview collects all current-inventory outcomes once, before its own notice
and protection audit writes, under one retained `BEGIN IMMEDIATE`/Database
RLock. It keeps that same fence through the 250-row keyset batches, without
COMMIT, await or lock release between projection and batch processing. The
bounded inventory and reference caps/deadline are unchanged. This avoids
reparsing the full live task/thread/skill projection for each batch: measured
7,523-file live data required about 2.85–3.29 seconds per proof, mostly text
parsing, so 31 separate proofs alone exceeded the 60-second pass budget.
Preview outcomes describe the inventory before the sweep's own notices; they
authorize no expiry or unlink. Enforce and off retain per-batch proofs, and
enforce deletion always obtains another fresh negative inside `finish_delete`'s
final writer fence after the intent COMMIT. No proof crosses that COMMIT.

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
(`manual`/`system`), `owner_kind`, `owner`, accountable `owner_agent_id`,
`origin_task_id`, `origin_thread_id`,
before/after state, reason, size, expiry, effective mode and outcome. Do not log
file contents, secrets, local source paths or invented historical provenance.

| Action | `audit_log.task_id` | Additional payload / when |
| --- | --- | --- |
| `artifact_put` | `artifact:<name>` | Preserve existing `name,size_bytes` keys; add metadata/generation. Only successful finalized publication. |
| `artifact_attribution_pending` / `artifact_attribution_bound` / `artifact_attribution_timeout` | `artifact:<name>` | Founder provisional publication / first attach binding / 24-hour unbound decision. Include human owner, accountable agent and routing reason, upload/bind/timeout times, before/after origin and expiry. Binding commits with thread/message/attachment; timeout receipt deduplicates manager routing. Never fabricate an origin. |
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

Both manager review of TASK-18706 and the seven-day preview are required before
applying any legacy classification. Accepted classifications are default/reference
with full metadata and an
explicit bounded future expiry measured from classification, or a reviewed
retire decision entering the seven-day recoverable path after preview/enforce
gates and live-reference checks. Leave undecided items legacy_unclassified.
The inventory import never physically deletes bytes. Per-item review provenance
remains required for subsequent legacy expiry/delete even after the org enables
enforcement. No unaudited backfill from artifact_put audit attribution, no
automatic 90-day assignment and no bulk age-based retirement. Canonical skill
sources follow decided Q3: verified version/reader dependencies defer expiry
and deletion, count toward quota, and are audited/rechecked each sweep. Their
exact reader-exclusion deferrals from a completed projection count as protected
`q3_deferred` rows without making the preview incomplete. Their
reference check must be implemented and verified before destructive activation;
this is not approval for indefinite exempt storage.

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
they are not canonical files to edit by hand. Revision TASK-18728 also inspected
the retained `web/` thread writer and repository-owned `skills/happyranch/`.

| Writer/instruction or consumer found | Disposition |
| --- | --- |
| `runtime/orchestrator/workspace_adapters.py::_shared_artifacts_section` (around 1483) | Concrete routine-evidence writer instruction: recommends screenshots and a QA screenshot example, plus metadata-free put. Replace those examples with `output/<actual-task-id>/` plus output_dir/recall and authorized fresh task attachment for binary handoff. Retain metadata-complete put only for persistent downloadable deliverables. Bootstrap regeneration uses supported enrollment/update/release paths, never edits managed agent instructions directly. |
| `runtime/skills/bundled/thread/SKILL.md` shared-artifact escape hatch (around 201) | Generic pre-upload recipe can route routine evidence to shared storage. Limit it to persistent shared deliverables with required metadata; routine logs/screenshots stay task output with supported task handoff. Explain the derived metadata for explicit `--shared`. |
| `cli/commands/threads.py::_merge_uploaded_attachments` and `cmd_threads_compose` | Actual automatic upload when `--shared` is explicit; no screenshot/log classifier exists. Keep explicit persistent sharing with complete derived metadata. Slice A replaces pre-ID CLI pre-upload with the composite server bind transaction. Instructions must stop selecting this path for routine evidence; do not silently move a user-requested shared file to private storage. |
| `web/src/features/threads/ThreadsPage.tsx:808-812`; `web/src/lib/api/artifacts.ts::uploadArtifact` / `uploadWithToken` | Retained browser thread attachment writer sends only file/name/founder. Slice A accepts the founder-bearer metadata-free request only provisionally and binds at attach with human ownership and manager accountability. Explicit thread_id/purpose follows in the separately owned frontend prerequisite after sibling page removal; no web edit here. |
| `skills/happyranch/SKILL.md:144-159` “Shared Artifacts” | Repository-owned instruction advertises screenshots and metadata-free put. Slice C replaces routine screenshot guidance with task output and metadata-complete persistent-file examples; reconciles overwrite/history/audit descriptions with A. This is not a materialized workspace skill. |
| `cli/commands/artifacts.py::cmd_artifacts_put`, `cli/client/client.py::put_artifact`, `runtime/daemon/routes/artifacts.py::put_artifact` | Generic upload chain, not a screenshot generator. Enforce complete metadata/quota and expose lifecycle errors; examples point routine evidence to task output. |
| `runtime/daemon/routes/custom_skills.py::_write_artifact` and compensation helpers | Actual automatic writer of canonical skill source, not routine logs/screenshots. **Do not reroute to task output.** Q3 is decided: verified version/reader reference protection in B, dedicated-store relocation in a separate engineering-owned PR. Preserve INSERT/write/COMMIT compensation ordering. |
| `runtime/infrastructure/artifact_store.py::put` | Generic blob writer; keep existing validation/atomic replace. Org shared service owns policy. Direct custom-skill callers cannot become an uncounted metadata bypass. |
| `runtime/skills/bundled/start-task/SKILL.md` task-output hygiene and attachment ownership guidance | Already routes task evidence to output and prohibits Shared Artifacts ownership workarounds. Preserve that policy and add explicit retention-aware persistent-file pointer; do not invent an upload requirement. |
| `runtime/skills/bundled/manage-agent/SKILL.md` | No artifact upload instruction found. Its bootstrap-regeneration section must point to the regenerated artifact policy; no roster/permission changes. |
| `runtime/orchestrator/run_step.py` completion guidance | Already says keep task-only evidence in output_dir/recall. Preserve; no routine shared writer found. |
| `docs/agent-guides/features-and-invariants.md` Shared Artifacts; mirrored attachment guidance in `orchestrator-contracts.md`, `runtime-and-configuration.md`, and `contracts/task-orchestration.md`; `CLAUDE.md`/`AGENTS.md` attachment rule | Existing prohibition is correct. Update API/retention statements and examples; routine evidence remains task output. The old page description is reconciled by the sibling removal, not copied into new instructions. |
| `runtime/daemon/app.py`, `routes/orgs.py`, `runtime/orchestrator/_paths.py` | Directory setup/path consumers, not routine file writers. No rerouting. |
| `runtime/daemon/routes/threads.py` attachment reader; `workspace_adapters.py` custom-skill materialization; `routes/skills.py` recovery; `db/schema.py::_retire_skill_lifecycle_if_present` | Readers or separately scoped deletion consumers. Integrate lifecycle lookup for thread reads; preserve source/retirement invariants and prove Q3 version/reader protection before destructive activation. |

No automatic routine screenshot/log *script* was found in the assigned search
roots beyond these instructions and explicit upload paths. This is not proof
about external custom tools, historical task scripts or other `web/`/`e2e/` writers;
the sibling inventory must reconcile any additional deployed writers before
rollout. Do not rename external outputs or claim they were fixed in this PR.
Task output follows its existing size/retention contract; this change does not
create a second indefinite evidence archive.

## Implementation PR slices and doc parity

Four ordered slices below have disjoint file ownership. A exposes the service
interfaces B and D consume; B and D do not patch A's schema/routes. Tables,
outbox and lifecycle transition methods needed later are installed in A with
destructive scheduling absent. Q1-Q7 are decided; design/implementation dispatch
may proceed after manager design acceptance. Destructive activation still needs
verified Q3 source protection, reviewed inventory and seven-day preview; D's
classification needs both inventory review and preview. If implementation needs
overlapping files, manager must reslice before dispatch rather than promise
disjointness falsely.

| Order / slice | Exclusive implementation files and responsibility | Same-PR documentation |
| --- | --- | --- |
| A — metadata, provisional binding and retrieval/admission seam | `runtime/infrastructure/db/schema.py`; new `runtime/infrastructure/db/shared_artifacts.py`, `runtime/infrastructure/shared_artifact_service.py`; `runtime/infrastructure/database.py` mixin wiring; `runtime/infrastructure/artifact_store.py`; `runtime/infrastructure/audit_logger.py`; `runtime/daemon/routes/artifacts.py`, `runtime/daemon/routes/threads.py`; `runtime/infrastructure/db/threads.py`; `cli/commands/artifacts.py`, `cli/commands/threads.py`, `cli/client/client.py`; `tests/contract/openapi.json`, `tests/contract/route-classification.json` if required. Strict agent/CLI metadata, founder provisional flow and 24-hour timeout seam, atomic bind-at-attach/shared compose with human owner and accountable manager, quota admission, journal/transition interfaces, renew/restore, exact overwrite generations, explainable GET/thread projections and audit helpers. No sweeper activation. | `docs/agent-guides/features-and-invariants.md` “Shared Artifacts”, “Shared artifacts” feature entry and thread attachment contract; `docs/agent-guides/web-and-cli.md` artifact CLI/API/error reference; new `docs/agent-guides/contracts/shared-artifacts.md` owns the final contract and imports/transition interfaces. |
| B — daily preview, reference proof and notices | New `runtime/daemon/shared_artifacts_scheduler.py`, `runtime/daemon/shared_artifacts_notices.py`, `runtime/infrastructure/shared_artifact_references.py`; `runtime/daemon/app.py` lifespan wiring; `runtime/orchestrator/org_config.py` flag parsing/validation. Uses A's transactional public service methods and audit helpers; no second metadata owner or edits to existing disk/workspace schedulers. Implements/verifies Q3 skill version/materialization/recovery reference checks and rechecked, counted, audited deferrals. Quota alert/dedup and provisional timeout routing use tagless full-JSON notices with actual inbox visibility evidence; admission enforcement already ships in A. | `docs/agent-guides/runtime-and-configuration.md` shared-artifact mode, timing, rollback and rollout gates; `docs/agent-guides/contracts/jobs-and-cleanup.md` distinguish artifact sweep authority from workspace/disk cleanup; `docs/agent-guides/contracts/README.md` link the contract. |
| C — generated writers and operating instructions | `runtime/orchestrator/workspace_adapters.py::_shared_artifacts_section`; `runtime/skills/bundled/start-task/SKILL.md`, `runtime/skills/bundled/thread/SKILL.md`, `runtime/skills/bundled/manage-agent/SKILL.md`; repository-owned `skills/happyranch/SKILL.md` “Shared Artifacts” (144-159 at baseline). Canonical-source relocation remains a separate PR. | Canonical `AGENTS.md` (“Task model rules” attachment ownership; “Agent-side rules” task-only evidence; “Feature pointers” shared-file contract pointer), exposed through the `CLAUDE.md` symlink. `docs/agent-guides/task-output-practice.md` binary handoff/routine screenshots/logs; `docs/agent-guides/orchestrator-contracts.md` attachment ownership mirror; `docs/agent-guides/project-layout.md` artifact metadata/service ownership. Reconcile the existing accepted KB `shared-artifacts-hygiene` mirror under engineering_manager with product_lead as wording owner: preserve both admission tests, SYSTEM exception, numbers and pending-enforcement label; explain the decided founder provisional/attribution contract and update rollout evidence only when verified. No new Product acceptance gate. |
| D — reviewed legacy classification | New `runtime/daemon/artifact_legacy_import.py`, `runtime/infrastructure/shared_artifact_inventory.py`; uses A's already installed review/metadata APIs and B's preview decisions. No schema or existing CLI edits. Applying classification requires engineering_manager acceptance of sibling TASK-18706 exact inventory **and** the seven-day preview; no import-side unlink. | New `docs/operations/shared-artifacts-classification.md`: reviewed manifest/review schemas, dry-run/apply invocation, exact-hash failure behavior and day-one quota accounting. |

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

Product's wording/SYSTEM exception is accepted (THR-394 seq2-3); the current KB
already reflects it. The appendix makes the reconciled text normative and keeps
seq1 only as history. This PR does not update the live KB. Manager dispositions
resolve routing, notice encoding, restore access and source dependencies; only
the stated implementation verification/rollout gates remain. engineering_manager
owns both separate follow-ups: explicit browser metadata after page removal
(worker frontend_engineer), and canonical-source relocation in an engineering PR.

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
| Provisional/binding / A | Current browser's founder-bearer file/name-only upload yields pending-attribution, human founder owner and engineering_manager accountability; no guessed origin. Agent/CLI metadata omissions still reject. Existing-thread attach and pre-ID compose atomically bind real THR/purpose/90-day expiry and manager; rollback/concurrent replay publish no partial attribution or duplicate grant. Unbound at exactly 24 hours gets upload + 90-day expiry and manager route even after downtime; expired provisional attach cannot silently restore. | Existing-client request shape through real upload/thread service with private DB/filesystem and controlled clock; future browser follow-up independently verifies explicit fields. |
| Overwrite/crash / A | Exact active replacement and generation history; case-alias/live-ref/legacy/tombstone refusals preserve old hash. Fail staging, rename, audit and COMMIT; reconcile only the exact journaled effect, never a peer file. | Real service/storage, private disposable dirs and controlled failure points. |
| Renew/restore / A | GET never renews; exact renewal maximum accepted, +1 instant refused; recoverable restore before boundary works, after boundary/deleted refuses; quota full still allows counted-byte restore. | CLI/API with controlled UTC clock and private data. |
| Tombstones / A | Original archived-thread URL/API/CLI get of expired/recoverable/deleted returns date/owner/reason, nonzero CLI with unchanged destination; never-known name remains 404. | Real retrieval and thread projection seam, no web/E2E change. |
| Quota / A+B | Active+recoverable+protected+legacy+reservations sum once; exact limit/+1, overwrite staging and concurrent uploads cannot oversubscribe; crossing→repeat→below→recross yields exactly two alerts across restart. | Concurrent service admission and real receipt/message transactions on isolated org. |
| References / B | Pending/queued/delegated task, open shared attachment and recognized text link hold expiry/delete; archived-only link does not. Every non-retired B2 version or materialization/recovery reader holds its source, counted/retrievable with an audited deferral rechecked every sweep; retirement alone is insufficient if a reader survives. Malformed/unavailable/capped scans hold, never empty. Reference arrival at final fence cannot race unlink. Unverified source check blocks activation. | Real DB readers/writers and filesystem, bounded concurrency barriers. |
| Sweep / B | Expiry→full seven-day recovery→actual unlink+tombstone; live hold removal and new recovery semantics; inode/hash/symlink replacement holds; interruption after unlink retains explainable journal. | Controlled-clock direct service/scheduler pass, private storage. |
| Preview/notices / B | First week, premature enforce, off/invalid config and incomplete/restarted passes never mutate bytes; one owner notice per expiry at minus seven days, late delivery truthful; unknown/retired/human owner uses recorded manager route. Both kind_tag and event are absent; the real Threads inbox displays full file name, expiry, owner, routing and renew/restore commands from payload; CLI show also prints them. Named unknown-tag/body-only messages do not satisfy delivery acceptance. Missing route/audit fails without false delivery. | Direct scheduler/notice service and actual existing renderer/inbox, synthetic roster/private thread rows; no ad hoc Product E2E or new trigger. Receipt persistence alone is insufficient. |
| Legacy / D | New daemon/old dir, old writer/new rows, 1.7 GB day-one accounting, unknown owner, changed inventory hash, missing manager review and reviewed retire decision; preview week alone cannot classify or activate destruction. No inferred expiry and no import-side unlink. | Private old/new schema/directories plus real import validation. |
| Instructions / C | Generated prompt and each maintained example routes routine evidence to task output and reserves shared put for complete persistent metadata; new bootstrap includes it without editing managed files. | Targeted source/generated-output inspection; no mirror-only test. |

State unsupported proof as NOT RUN, never PASS. The reviewed inventory,
seven-day preview and exact-source compatibility/activation evidence are
future release gates, not satisfied by a design review or a green lint run.

## Appendix: Proposed product wording

### Normative contract — reconciled with accepted THR-394 seq2

**Founder-approved; enforcement pending verified rollout.** Product accepted
the six-rule contract with the following clarifications in THR-394 seq2;
engineering_manager accepted/applied them to KB `shared-artifacts-hygiene` in
seq3. This text governs the design; the seq1 draft below is historical only.
Acceptance is not evidence of deployed enforcement and is no longer an open
Product prerequisite. This contract explicitly supersedes the former statement
that there is no automatic TTL policy.

Keep both KB admission tests: (1) the file is needed beyond its originating
task; (2) it needs downloadable-file delivery rather than KB text. Passing both
admits a bounded retention grant, never permanent retention. A historical
citation alone does not establish permanent retention.

1. **Metadata.** New attributed shared files carry an owning agent (or human
   owner with accountable agent), originating task/thread, purpose and expiry.
   Agent/CLI puts missing required metadata are refused. The decided founder
   browser compatibility flow is provisional pending-attribution only, binds
   real origin/purpose/expiry server-side at attachment, and bounds unbound
   uploads by the 24-hour/upload-based-expiry rule above. It is not a general
   metadata-free upload path.
2. **Bounded grants.** Measure each expiry from upload or explicit renewal;
   ordinary retention is at most 90 days per grant, eligible design/reference
   sources up to 180 days with a named owner and reason. Binding completes the
   provisional upload's attribution and starts its specified 90-day grant.
   Renewal requires continuing use and a new bounded expiry with the same
   maximum. Recovery also requires continuing use and a new bounded expiry.
   Downloads and cleanup never silently renew. No permanent exemption.
3. **Notice, daily cleanup and recovery.** Cleanup runs daily. Notify the owner
   seven days before expiry, including manager routing where applicable.
   Recovery lasts seven days from the actual expired/unavailable transition,
   not the earlier scheduled expiry while dependency protection kept the file
   retrievable. Ending protection cannot cause immediate irreversible deletion.
   Thereafter delete backing bytes and retain explanatory date/owner/reason
   responses for expired/deleted references.
4. **Quota.** 5 GB per org (5,000,000,000 bytes), alert at 80%
   (4,000,000,000 bytes); refuse new uploads at the limit, preserve downloads.
   Count recoverable and dependency-protected bytes, provisional and legacy
   bytes and reservations; no state creates a quota discount.
5. **Narrow SYSTEM exception and rollout.** Owner-prunes-own is amended only
   for audited SYSTEM cleanup under this contract. Verified running/queued-task
   dependencies and open-thread links defer expiry and byte deletion, remain
   retrievable, count toward quota, and are audited and rechecked every sweep.
   Q3 applies the same live-dependency safeguard to skill version/reader source
   references; it is not a permanent retention class. The first week is preview
   only. Pre-change files stay outside automatic expiry until the ownership/
   dependency inventory and deletion preview receive manager review; seven
   preview days alone do not satisfy that gate. Per Q7, both reviewed TASK-18706
   inventory and seven-day preview precede legacy classification or destructive
   activation; verified source checks are also required for activation.
   Unknown/retired owners route to the responsible manager. Direct host
   filesystem deletion remains prohibited; there is **no general agent delete
   command**. The separately audited SYSTEM lifecycle does not grant one.
6. **Routine evidence.** Routine screenshots/logs and task verification evidence
   go to task output, never Shared Artifacts. Durable knowledge belongs in KB;
   persistent downloadable deliverables must pass both admission tests above.

### Historical draft — THR-394 seq1 (not normative)

Verbatim draft additions, retained for history as requested. Where they differ,
the seq2-reconciled normative text and decided compatibility contract above apply.

**Draft additions**
1. Every new shared file carries owning agent, originating task or thread, purpose and expiry. Uploads without them are refused.
2. Default expiry 90 days. Design sources/reference files may have up to 180 days with a named owner and reason. Owners may renew with a stated continuing use and a new expiry (same maximum). Downloads never renew. No permanent exemption.
3. The owner is notified 7 days before expiry. Expired files are recoverable for 7 days, then their bytes are deleted. References to expired/deleted files explain the expiry (date, owner, reason).
4. Quota 5 GB per org; alert at 80%; new uploads refused at the limit; downloads keep working. Recoverable files and live-task dependencies count.
5. **Owner-prunes-own amendment:** agents still prune only their own files, except a narrowly scoped, audited SYSTEM cleanup may expire, recover and delete files under the rules above. It protects verified live-task dependencies (running/queued tasks, open thread links). Existing pre-change files are not expired until an inventory/preview is reviewed; unknown-owner files go to the responsible manager. The first week is preview-only.
6. Routine screenshots/logs go to task output, never Shared Artifacts.
