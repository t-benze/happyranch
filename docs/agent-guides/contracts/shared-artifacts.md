# Shared Artifacts contract and transition interfaces

Normative policy: [Founder-approved retention design](../../specs/2026-11-23-shared-artifacts-retention.md)
(THR-392 seq13/14). Slices A/B are source delivery only. They do not establish
live rollout, notices, legacy classification, or destructive activation.

Files must be needed after their originating task and require downloadable-file
delivery. Routine screenshots/logs and task verification stay in task output;
durable text knowledge belongs in KB. Retention is bounded, with no permanent
exemption: 90 elapsed UTC days by default; design/reference sources up to 180 days
with a named owner and nonblank reason. Explicit renew/restore requires continuing
use and a new bounded expiry from the action. Downloads never renew.

New agent/CLI puts require permanent owning agent ID, exactly one same-org task or
thread, nonblank purpose and `default`/`reference` retention. Optional UTC expiry
can shorten the grant; reference requires reason. Metadata errors precede durable
bytes/rows. Org bearer authority stays unchanged; the agent string is attribution,
not identity proof. Renew/restore use existing org-bearer access, recording actor.

The sole metadata-free browser compatibility request is founder-bearer upload
with `agent=founder` and no supplied attribution fields. It creates a provisional
`pending-attribution` generation, human owner `founder`, accountable
`engineering_manager`, with no guessed origin/purpose. At upload +24 hours the
service fixes expiry to upload +90 days and records a pending manager-routing
receipt, even after downtime. First available attachment binds the real THR ID,
`thread attachment` purpose, responsible manager and bind +90-day grant in the
message transaction. Repeated links do not grant another clock. An expired
provisional cannot silently bind/restore.

CLI shared compose sends files with its existing multipart compose endpoint plus
`shared_uploads=true`. The server allocates the real thread ID and derives full
human/agent metadata inside the composite transaction. No draft ID or preliminary
public empty thread is used. Private thread-scoped attachment storage remains
separate. New shared links pin generation; historical links without pins resolve
the current name. History keeps unavailable attachments and explanatory metadata.

Composite shared and private uploads persist a custody intent before the thread
transaction can publish bytes. The same Database RLock spans that intent COMMIT,
the thread transaction and compensation. A rollback restores the prior durable
intent; compensation deletes only a captured, still-matching request identity.
Post-publication stat/identity failure, replacement bytes or failed cleanup retain
an unfinished intent, even when no generation/thread row committed. Shared GET
returns `503 artifact_operation_pending` before any legacy discovery; quota scans
count the held physical bytes without adopting them. Private receipts retain the
thread/attachment IDs in this journal and never enter shared quota as private
bytes. These intents require exclusive recovery; the ordinary put reconciler
cannot finalize a rolled-back compose. A successful thread COMMIT commits the
shared generation/operation and removes private custody receipts.

## Storage and quota

`ArtifactStore` stays policy-free. `SharedArtifactService` owns org metadata,
admission, journal, identity checks and explicit grants. Additive tables are
installed by `SchemaMixin`; existing layouts are validated before use. Existing
blobs become `legacy_unclassified` with null expiry, never mtime +90 days. Old
writers remain incompatible with enforced policy; changed/missing known bytes
hold reconciliation/admission and stay explainable. No directory relocation or
live migration is included.

Quota is exactly **5,000,000,000 bytes**, alert threshold **4,000,000,000**.
Active, recoverable, protected, provisional, legacy and uncertain physical bytes
plus unmaterialized reservations count once. Full incoming replacement size is
reserved while old bytes still exist. Exact-limit admission is allowed; +1 is
`413 artifact_quota_exceeded` with used/reserved/requested/quota and guidance.
GET/list and already-counted restore/renew continue at quota. A complete bounded
no-follow size/hash scan precedes admission (100,000 entries /60 seconds); an
incomplete or unsafe inventory returns `503 artifact_usage_unavailable`, never a
quota discount. Unknown in-flight identity remains counted and held.

Ordinary put journals/reserves, creates private same-filesystem staging, records
its identity, prepares, atomically publishes and then finalizes metadata and
`artifact_put`. A publication is unavailable until final COMMIT. Final audit or
COMMIT failure retains the prepared intent; exact inode/hash recovery finalizes
once. Request-owned staging compensation checks recorded identity; it never
unlinks an uncertain public file. Retry uses the same UUID operation ID and
original generation; divergent input is refused. Exact active replacement keeps
old generation metadata with reason `replaced`, without a prior-byte recovery
promise. Legacy and unavailable names cannot be reused. Case-alias validation is
unchanged.

**Reference seam:** B installs `db._shared_artifact_reference_checker(conn,row)`
returning `absent`, `live` or an indeterminate result, and
`db._shared_artifact_effective_mode(conn)`. A never infers a negative proof from an
absent scanner. Overwrite is held with `503 artifact_reference_check_unavailable`
until the complete checker is installed; `live` refuses with
`409 artifact_live_dependency`. This source-only seam must be integrated and
verified by B before replacement rollout. The same writer fence spans the final
proof, byte publication and metadata commit.

## Canonical custom-skill source exception

The existing canonical writer preserves **version INSERT in caller BEGIN
IMMEDIATE → admission rows → blob write → caller COMMIT**, with caller-owned
compensation armed through COMMIT. `admit_in_caller_transaction` takes that same
Database connection and never opens/commits a transaction. Quota refusal precedes
blob write and rolls back with the version. Custom-skill caller transactions
hold the same Database RLock from BEGIN through their COMMIT/rollback, so artifact
readers/writers wait instead of joining or rejecting another caller's transaction.
Lock order is existing session lease (agent only), completed native writer
admission, existing canonical publication barrier where applicable, then Database
RLock. The short native move gate is released before that RLock interval; no move
gate or await is acquired within it. Generic store operations never
locate an ambient org/DB.

Metadata is server-derived: human actor → human Founder and accountable
`engineering_manager`; agent actor → its permanent ID. Provided task ID remains
origin. **The single named origin-XOR exception** is a canonical custom-skill
source without any task/thread origin: it is explicitly `system-derived`, with
purpose naming the skill slug and version ID. It receives default bounded expiry
and a verified-live source dependency marker. B must independently verify every
non-retired version and surviving materialization/recovery reader; this marker
is not a permanent retention class or sufficient source-deletion proof.
A process crash before the caller COMMIT retains the existing two-store
uncertainty; uncommitted SQL cannot survive it. Discovered unjournaled residue is
legacy/held, never silently assigned an expiry or deleted.

## B and D imports

Use `SharedArtifactService` and `SharedArtifactsMixin`, not direct counter owners.
All `*_uncommitted` calls require the same org Database connection inside its
`shared_artifact_transaction()` (BEGIN IMMEDIATE, shared RLock, rollback through
COMMIT). Do not call committing thread append methods within it.

| Interface | Caller contract |
| --- | --- |
| `reconcile_usage_uncommitted(conn)` | Complete bounded inventory, legacy registration and `(physical,reserved)` accounting; no TTL classification. |
| `quota_crossing_uncommitted(conn)` | Durable latch/sequence. B must commit its alert message/receipt/audit in this same transaction, so retries cannot lose or duplicate crossings. |
| `lifecycle_transition_uncommitted(...)` | B supplies exact revision, operation ID, reason, effective mode and same-fence full reference outcome. Timeout, preview, protection, recovery and final deleted metadata only; no byte unlink. |
| `shared_artifact_notice_uncommitted(...)` | One tagless full-JSON system message, receipt and audit; replay returns original sequence. Quota notices delegate to the atomic chunk helper below; the singleton wrapper still returns the first sequence. B supplies verified destination and complete visible payload; no delivery activated here. |
| `shared_artifact_quota_alert_uncommitted(...)` | Caller owns the crossing transaction. All ordered tagless chunks carry crossing_sequence, zero-based chunk_index and chunk_count; one existing receipt key `(empty name, crossing sequence, empty expiry, quota_alert)` stores chunk_count/message_seqs/payloads. Replay returns the stored sequences (old singleton receipts return their original sequence). One config-scoped artifact_quota_alert retains message_seq=first and adds chunk_count/message_seqs. Escaped JSON is at most32KiB per message and at most half the SQLite length limit; no schema change. |
| `prepare_delete_uncommitted(...)` | B persists exact identity/revision/hash intent before its separately fenced unlink; A never unlinks public bytes. |
| `reconcile_operation(operation_id)` | Reconcile only positive exact staging/publication identity. Uncertain or peer bytes stay held; no generic retry/unlink. |
| `record_inventory_review_uncommitted(conn,review=...)` | D supplies authenticated immutable digest/reviewer/task/citation/decisions/time receipt; updates/deletes are prohibited. |
| `classify_legacy_uncommitted(...)` | D validates exact item decision and full attribution, then applies only matching current hash with reviewed inventory and seven-day preview gates. No import-side unlink. |

The org-state singleton holds rollout start, preview acceptance, reviewed
inventory, source-proof verification, completed window and quota latch. B owns
configuration/timing and validated reference scans; D owns reviewed manifest
validation and classification. A's transition seam refuses mutation without
enforce mode, accepted preview, reviewed inventory, source verification and seven
elapsed preview days. Legacy remains outside automatic expiry. Expiry starts a
full seven-day recovery at the actual unavailable transition. Preview/off never
start an unavailable interval merely because a date passed. DELETE uses the same effective-mode/reference gates: preview/off returns a
nonmutating preview; gated enforce enters recoverable retirement. Only the
scheduler later unlinks through the durable intent and final writer fence.

GET resolves generation metadata before bytes. Unavailable rows return
`410 artifact_unavailable` with date, owner, reason, recovery/deletion dates and
`can_restore`. Known missing/mismatched bytes use explanatory storage/reconciliation
states, never a bare unknown-file 404. A never auto-renews. The existing browser can continue following unversioned name links; the API also supplies a generation URL for clients that use the pin. CLI get exits nonzero
and preserves its output on refusal. Tombstones remain listed; API supports
`available_only=true`. `artifact:<name>` and `config:shared_artifacts` audit scopes
retain their existing meanings; task/thread provenance lives only in payload.

## Verification

`uv run --frozen python scripts/test_shared_artifacts.py` runs finite private
ASGI/SQLite/filesystem cases without a daemon lifespan, listener, provider or live
org. `--case metadata|provisional|journal|custom|quota|shape` selects a group.
B uses `uv run --frozen python scripts/test_shared_artifacts_b.py --case` with
`all|manual-delete|quota-chunks|references|history|live-cap|notices|sweep|sweep-shape` (default all). This is a
standalone slice-owned script (including a7500-file /1.7GB sparse legacy hash/SQL shape case) outside the retired Python suite. It adds no CI
gate or E2E trigger. Exact results, limitations and sensitivity evidence belong
to the task/PR. The [TLA+ model](../../specs/formal/shared-artifacts/README.md) is
bounded review evidence, not proof of Python execution or activation.

## Slice B reference projection (source only)

`shared_artifact_references.reference_outcomes` reads the complete bounded live
projection under the caller's retained writer transaction. It first reads only
task IDs, statuses and parent IDs to select `pending`, `in_progress` and
`escalated` tasks plus their direct children. It then loads only those tasks'
briefs, final summaries and results, following explicit TASK handoffs to closure
with a visited set. Unrelated closed task/result text never consumes the32MiB
text bound; required live-closure text exceeding that bound still makes the
proof indeterminate. The100,000-row and shared deadline bounds also cover the
task metadata scan; unknown statuses and missing/malformed required handoffs
remain indeterminate. Open-thread and skill-source projection is unchanged.
Foreign-org qualified task paths are not same-org handoffs.
Outcomes are collected
before own audit writes; no snapshot survives COMMIT or lock release. The final
lifecycle/unlink caller must retain that fence through its decision. Structured
shared attachments preserve generation pins. Non-retired B2 versions and every
materialization record hold their source. Retired sources lacking authoritative
in-flight reader exclusion remain indeterminate; retirement does not authorize
deletion. Recovery selects an eligible current version before reading its source,
so the same conservative hold covers a previously selected recovery reader.
Neither this module nor installation sets source_protection_verified, preview
acceptance or inventory review. Full rollout remains separately held.

## Slice B notices (source only)

`SharedArtifactNotices` uses A's atomic notice/quota writers. Receipt-linked
thread IDs are reusable only with open system provenance, matching notice
channel and exact active participants. Founder is visible through the existing
inbox and is never an agent participant. Expiry identity is generation plus
expiry; due time, actual delivery time and lateness are explicit. Human, unknown
and retired owners use recorded accountable agent, origin-team manager, then
engineering_manager; an unavailable route remains pending without false delivery.
Timeout notices use upload +24h and upload +90d expiry even during preview,
without changing lifecycle metadata. A queued timeout receipt survives later binding;
its historical upload-based expiry and the current expiry are both visible. Quota crossings include every file entry
in bounded full-JSON chunks; legacy entries direct managers to reviewed inventory
classification, with renewal/restore commands unavailable until classified. All
managers participate in one storage
thread; chunk messages/receipt/audit and latch commit together. Required fields
are in tagless system_payload, which the existing inbox and CLI show in full.
`uv run --frozen python scripts/test_shared_artifacts_b.py --case notices`
checks the real private API inbox and CLI rendering. QA browser rendering remains
independent acceptance; source delivery does not activate live notices.

## Slice B daily lifecycle (source only)

`shared_artifacts_scheduler` runs at04:00 org-local time after30s warm-up, takes
the latest real occurrence (fold0; nonexistent occurrences skipped), and claims
one persisted audit window. It uses250-row keysets and one60s monotonic deadline
passed through inventory, reference, notice and byte-identity work. An incomplete
pass records its cursor; unseen work waits for a later daily window. Only issued
delete intents are reconciled within a serviced window; incomplete destructive
passes are not replayed. Native work retains org lifetime and cancellation
custody through its terminal result before database close.

A's day-one accounting may register previously unseen legacy blobs; preview
never changes existing lifecycle state, expiry, recovery interval or bytes. It
can persist notices, audits, quota latch, inventory observations and a completed
preview start. Legacy null-expiry files remain unclassified and are never
automatically expired/deleted. A live reference holds expiry; removal starts a
full7d recovery at the actual transition. After recovery, A commits intent and
audit before B reacquires a fresh complete reference proof and exact no-follow
identity under one writer fence through unlink, tombstone and audit. Missing
bytes can reconcile only a previously issued intent; changed inode/hash/symlink
bytes remain held. Final failure leaves the durable issued intent explainable.
`reconcile_usage_uncommitted`, `quota_crossing_uncommitted` and
`prepare_delete_uncommitted` accept an optional shared deadline; omitted keeps
their prior behavior. `lifecycle_transition_uncommitted` accepts optional
reclaimed_bytes for B's final deletion audit; omitted keeps its prior payload.

Mode defaults to preview. YAML off must be written as `cleanup_mode: "off"`;
boolean/invalid/unreadable config holds mutation. Enforce requires7 elapsed
preview days, explicit preview acceptance, inventory review and independently
verified source protection. Source delivery sets none of those approval flags.
Turning preview/off stops new destructive decisions; issued effects retain
journal custody. No live activation, deploy or restart is part of this PR.
