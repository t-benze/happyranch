# Custom Task Attributes — storage, HTTP, creation, decisions and CLI

Status: PR1 foundation, PR2 HTTP adapters, PR3 plain creation/CLI and PR4 manager decisions;
THR-390 / TASK-19073 / TASK-19160. Other creation producers and UI remain
subsequent slices. No definitions are seeded.
The source contract is `runtime/task_attributes.py`,
`runtime/infrastructure/db/task_attributes.py`, and the additive installer in
`SchemaMixin._create_tables`. This spec implements PRD v1.0 F1–F6 storage portions
and engineering notes A4/A5/A7. Authentication and task ownership checks belong
to the HTTP adapters below; internal actor inputs are trusted, not authenticated
by this store. Storage is isolated by the existing per-org database path.

## Validation

Keys match `[a-z][a-z0-9_]{0,63}`. Every historical key stays reserved to its
stable definition ID, including pre-use renamed-away and archived keys. Other
definitions cannot create or rename onto those keys. Display names are trimmed
at the outside and contain 1–80 code points; duplicates are allowed. Description
is optional (`None`), with exact content up to 4,000 code points.

Types are exactly `numeric`, `string`, `boolean`:

- Numeric accepts native Python int or finite float, never bool or a numeric
  string. Bounds `min`/`max` are optional finite native numbers, inclusive and
  coherent. `integer_only` is an optional native bool; `4.0` is integral. A
  bounded integer-only interval must contain at least one integer. No numeric
  magnitude cap or float conversion of stored integers is imposed.
- Strings preserve exact content (including empty, whitespace and Unicode).
  Intrinsic maximum is 4,000 Unicode code points. Optional `min_length` and
  `max_length` are native integers in 0–4,000 (never bool), inclusive and coherent.
- Boolean accepts only native true/false and has no constraints.

No regex, enum, required/default or other constraints are accepted. Whole-set
`validate_values` accepts a map or key/value pairs; pairs preserve duplicates
before dict collapse. It collects field-specific errors for duplicates, null,
unknown/foreign/archived keys, wrong types and bound failures. Empty maps are
valid. Successful validation returns definition IDs/versions and original
native values. Store-backed validation reads current definitions but never
allocates rows, freezes a definition, commits or rolls back caller work. It is
advisory; writers validate again in their own transaction. Example integer
1–10 fields accept 4, 6 and 4.0; reject `"4"`, true, null, 0, 11 and 4.5.

## Additive schema manifest

All tables are created with `CREATE TABLE IF NOT EXISTS` on fresh and existing
DB open, alongside the `thread_guests` precedent. No old column/meaning changes,
deletes, audit-scope reuse, original-estimate backfill or live migration is
performed. TEXT PKs below are explicitly NOT NULL. All other columns are NOT
NULL except those explicitly marked nullable. Integers are SQLite INTEGER;
JSON scalar bytes are stored in TEXT to retain large integers and native types.

| Table (prefix `task_attribute_`) | Columns and keys |
| --- | --- |
| `definitions` | `definition_id` TEXT PK; `current_version` INTEGER >=1; `created_at` TEXT; nullable `archived_at`, `first_used_at` TEXT. Composite `(definition_id,current_version)` FK to versions, DEFERRABLE INITIALLY DEFERRED. |
| `definition_versions` | `definition_id` TEXT FK to definitions; `version` INTEGER >=1; `key`, `type`, `name`, `constraints_json`, `recorded_at` TEXT; nullable `description`, `archived_at` TEXT. PK `(definition_id,version)`. Type CHECK numeric/string/boolean. Full metadata snapshots, never deltas. |
| `key_owners` | `key` TEXT PK; `definition_id` TEXT FK to definitions; `reserved_at` TEXT. Append-only owner for every historical key. |
| `admissions` | `task_id` TEXT PK FK to tasks; `actor_id`, `actor_kind`, `admitted_at` TEXT. Immutable positive admission evidence, including empty input. |
| `initial_values` | `task_id` TEXT FK to admissions; `definition_id` TEXT; `definition_version` INTEGER; `value_json` TEXT. PK `(task_id,definition_id)`; composite definition/version FK. Only supplied values appear. |
| `values` | `task_id` TEXT FK to tasks; `definition_id` TEXT; `definition_version`, `revision`, `present` INTEGER; nullable `value_json` TEXT; `updated_at` TEXT. PK `(task_id,definition_id)`; composite definition/version FK. Revision >=1; presence 0/1. |
| `value_events` | `event_id` INTEGER PK; `task_id` TEXT FK to tasks; `definition_id` TEXT; `definition_version`, `revision`, `old_present`, `new_present` INTEGER; nullable `old_value_json`, `new_value_json`, `note` TEXT; `actor_id`, `actor_kind`, `at`, `source` TEXT. Composite definition/version FK; UNIQUE `(task_id,definition_id,revision)`; revision >=1 and presence 0/1. Source CHECK admission/edit/clear. |

Initial JSON must be valid non-null scalar JSON. Current/event presence CHECKs
require SQL NULL exactly when absent; present values are valid scalar JSON of
SQLite JSON type integer/real/text/true/false (never JSON null/array/object).
Domain types/constraints are additionally enforced by the pure validator.
Foreign keys use NO ACTION, never cascading retained history away.

Named indexes:

| Index | Columns |
| --- | --- |
| `idx_task_attribute_definitions_active` | archived_at, definition_id |
| `idx_task_attribute_versions_key` | key, definition_id, version |
| `idx_task_attribute_keys_owner` | definition_id, key |
| `idx_task_attribute_values_definition` | definition_id, task_id |
| `idx_task_attribute_events_task` | task_id, event_id |

Each of definition_versions, key_owners, admissions, initial_values and
value_events has `<full_table_name>_immutable_update` and
`<full_table_name>_immutable_delete` BEFORE triggers raising ABORT. Definitions
and current values remain mutable by their writers. PK/UNIQUE autoindexes have
SQLite-assigned names; their column order/uniqueness is the contract.

## Store API and transactions

`Database` exposes `create_task_attribute_definition`,
`get_task_attribute_definition(version=...)`, `list_task_attribute_definitions`,
`update_task_attribute_definition(expected_version=..., **changes)` and
`archive_task_attribute_definition(expected_version=...)`. Discovery defaults
to active definitions; historical reads opt into archived ones or a version.
Every successful definition edit/archive appends a complete version. Stable ID
and all old versions/keys survive rename/reword/archive. Key/type/constraints
freeze after any successful assignment, including admission, even after clear.
Active name/description can still change. Archived definitions reject metadata
edits and all value set/change/clear; no delete or restore API exists.

`set_task_attribute(task_id, definition_id, value, expected_revision=...,
expected_version=..., actor_id=..., actor_kind=..., note=None)` and explicit
`clear_task_attribute` are self-contained writers. They hold the instance RLock,
refuse an existing caller transaction, and use BEGIN IMMEDIATE/commit/rollback.
Task existence, current definition version/archive, native validation and current
revision are compared inside that writer transaction. Other connections cannot
pass a stale comparison between validation and update. A clear retains a
monotonically increasing tombstone revision; revision 0 means no current row.
It removes presence explicitly, never assigns null. Clearing an already-unset
field records an explicit absent-to-absent clear and advances revision, without
marking first assignment. Failed writes roll back current, event and first-use
changes together. Structure edits also recheck first use inside their writer.
The store adds no task lifecycle or ownership policy.

`_write_task_attributes_initial_uncommitted(task_id, values, actor_id=...,
actor_kind=...)` requires a caller-held RLock and open writer transaction, after
real `_insert_task_uncommitted`, as in `persist_root_task`. It refuses outside a
transaction, duplicate admission, and a task with existing value history. It
validates the whole set before attribute writes, records an immutable envelope
and supplied snapshots, and writes admission events/current values/freeze in the
same transaction. A savepoint cleans up its own partial writes on failure;
it never commits or rolls back earlier caller work. Caller alone controls task,
other rows and final commit/rollback. Later creation adapters must validate the
whole operation before allocation and call this primitive within admission;
PR1 does not claim that producer behavior is wired.

Store errors carry stable `code`; conflicts use `AttributeConflictError` with
`definition_version_conflict` or `value_revision_conflict`. Validation errors
carry `errors`, a field-to-error-list map. SQLite/read errors propagate distinctly
from valid empty history. These are internal contracts, not HTTP status mappings.

## Provenance and history (A7)

`get_task_attribute` returns current definition metadata, current presence,
immutable initial meaning/version, admission, provenance and history. Historical
versions in initial/events resolve old names, types, descriptions and constraints.
`get_task_attribute_history` returns a fixed object, never a bare array:

```json
{"history_state":"complete","events":[]}
```

Current/event presence is `{"present":false}` or
`{"present":true,"value":4}`. Current also carries revision (0 for never
recorded), and recorded current rows carry definition_version/updated_at. False,
0 and empty string remain present. Initial state is `set` with native value,
definition_version and definition metadata, `unset`, or `unknown`.
Admission is `recorded` with actor_id/actor_kind/at or `unavailable` with no
fabricated actor/time. Actor kind includes founder; timestamps are server UTC
with timezone. Optional note appears in events only when recorded.

| Facts | `provenance` | `initial.state` |
| --- | --- | --- |
| Real supplied admission snapshot | `supplied_at_admission` | set |
| Recorded admission plus reliably pre-existing definition | `not_supplied_before_dispatch` | unset |
| Definition reliably strictly later than task | `definition_added_later` | unset with admission; unknown without admission |
| No admission and definition pre-existing, or unprovable ordering | `timing_unknown` | unknown |
| Admission but no supplied snapshot and unprovable definition ordering | `timing_unknown` | unknown |

Classification uses timezone-aware instants, not timestamp-string sorting.
Missing/malformed/naive times and same-instant timestamps without independent
order cannot establish pre-existence or later timing. Supplied snapshot is
authoritative; reliable later-definition knowledge survives missing admission.
Known omission requires positive admission and pre-existence evidence. A
supplied initial snapshot referencing a definition created after its recorded
admission is a data error; definitions added after an empty admission are normal.
Later first assignment is always `edit`, never inferred pre-dispatch, and never
creates an original/admission envelope. No history is derived from audit_log.

History states: `complete` means recorded admission, including known empty;
`unavailable` means no admission and no events; `incomplete` means no admission
but a real recorded suffix. Events always remain an array. Each event carries
ID, definition ID/version and historical metadata, revision, actor_id/actor_kind,
UTC at, source (`admission`, `edit`, `clear`), old/new presence and optional note.
Order is monotonic event_id, never formatted time. Legacy edit→edit→clear retains
that exact suffix with incomplete history; rename/archive retain it as well.
Read projections use one SQLite snapshot and never end a caller transaction.

## Focused verification

Accepted case design: dev_agent TASK-19081 `case-design-v2.md`, SHA-256
`93874205063d3fc14570fde3bf4dafd8a4d53c5f3e22d40782cd01000b588efe`, independently
PASSed by QA TASK-19088 and accepted by engineering_manager TASK-19073.
`scripts/test_task_attributes.py` implements C01–C18 in the existing standalone
SQLite/unittest venue. It includes real constructor upgrade/rerun, independent
schema/FK/index/trigger manifests and retained audit bytes, typed roundtrips,
pre-BEGIN interleavings with separate connections/Events, caller sentinel
visibility, event-insert faults and per-table UPDATE/DELETE attacks. No production
hooks, daemon/listeners, live DBs, required test checks or new dependencies.

## PR2 HTTP routes

Accepted case design: TASK-19119 `case-design.md`, SHA-256
`41eb3760f1731c9f09036ea02cc3b9549cfc5d4e3a4df7b8bfc7946871589692`,
independently design-PASSed by TASK-19121 and accepted in TASK-19123's brief.
The adapter is `runtime/daemon/routes/task_attributes.py`; closed wire models
are in `task_attribute_models.py`. Prefix all paths with `/api/v1/orgs/{slug}`.
Every operation uses `Cache-Control: no-store` and is classified included in
the existing native surface inventory. Handwritten TS clients mirror them.

| Method | Path | Contract |
| --- | --- | --- |
| GET | `/task-attributes/definitions` | Active definitions by default; `include_archived=true` retains archives. Full rubric, constraints, version and definition editability. |
| GET | `/task-attributes/definitions/{definition_id}` | Stable-ID current lookup or positive `version` historical lookup. Editability always reflects current state. |
| POST | `/task-attributes/definitions` | Founder create with `expected_version:0`; 201/version 1. |
| PATCH | `/task-attributes/definitions/{definition_id}` | Founder partial update with positive `expected_version`; at least one change. Omitted metadata preserved; description null clears. |
| POST | `/task-attributes/definitions/{definition_id}/archive` | Founder archive with positive `expected_version`; no delete, restore or key reuse. |
| GET | `/tasks/{task_id}/attributes` | Task-level editability and active fields plus archived fields with task evidence, each with current, initial, admission, provenance, full history and field editability. |
| PUT | `/tasks/{task_id}/attributes` | Atomic nonempty unique-ID `changes` batch, with per-field version/revision preconditions. One-element batch handles a single edit. |
| GET | `/tasks/{task_id}/attributes/history` | Full event-id-ordered history; optional `definition_id` filter. |

Discovery accepts the existing bearer or current org-bound `session_id` query.
Task read/history/save retain the existing task bearer gate; a session alone
does not gain task read access. Session presence always selects the agent path,
even with a bearer. Unknown/replaced/recovery contexts never fall back to
Founder. Definition mutations reject agent binding context and use the existing
human bearer boundary unchanged. That bearer cannot cryptographically identify
a human token holder; PR2 makes no stronger identity claim.

Task edits accept Founder, current task owner, the target team's current
registered executable manager, or a current registered executable manager owning
the immediate persisted parent. Manager registration must be unambiguous and
agree with the active canonical agent definition. Generic manager roles,
historical creators, siblings, revisit and transitive ancestry grant nothing.
The native caller task/session must still be current, nonterminal and outside
recovery. Completed/failed/cancelled/superseded **targets** are not denied solely
for status; Pause introduces no metadata ban. Read and save share this policy.

`editability` is `{can_edit,refusal_reason:null|{code}}`. Stable reasons include
`not_task_owner_or_owning_manager`, `authority_unknown`, `session_not_current`,
`recovery_purpose_forbidden`, existing caller `task_not_active`, and field-level
`definition_archived`. An existing authorized reader with unusable agent
authority can read with `can_edit:false`; save returns the same reason in 403.

The existing authority async gate, teams lock and async DB lock precede the
short synchronous publisher, canonical-roster, binding, move and DB interval. No await occurs
under synchronous leases or the SQLite transaction. Current tracker generation,
native caller/target rows, canonical roster and owning relation are rechecked
after the actual `BEGIN IMMEDIATE`, and relevant guards survive COMMIT.
Reads assemble authority/definition/current/initial/history in one SQLite
snapshot. Optional current names are read-only decoration; failures there yield
null names without changing actor IDs/kinds or inventing history.

Save shape:

```json
{"changes":[{"definition_id":"stable-id","expected_version":1,"expected_revision":0,"op":"set","value":4}]}
```

`clear` forbids value; `set` requires a non-null native scalar. Optional string
note is recorded only when supplied. Strict integer CAS, unknown fields,
duplicate JSON members, duplicate query keys and duplicate change IDs are
rejected with 422 `attribute_validation_failed` and field errors. No scalar or
CAS coercion occurs. Whole-batch definition/version/archive/revision checks
precede aggregate scalar validation and all mutations. Unsubmitted fields remain
untouched. Initial/admission evidence is never created by edits.

Stale version/revision, first-use freeze, archive and reserved-key collisions
return 409 with an authorized concrete `current` observation. Definition errors
carry `{definition,editability}` by stable ID, including current first-use and
archive state even after rename; value errors carry the full task projection
and revision conflicts where applicable. Archived unused fields are included
in their refusal projection. A subsequent reload reauthorizes and reads again;
no automatic replay or silent constraints discard occurs. Missing/foreign IDs
share sanitized 404 `not_found`; no foreign data or org inventory is returned.
Storage/projection errors are 503 `attribute_storage_unavailable`, never empty
success. A lost response after COMMIT requires readback and deliberate CAS
handling, since transport loss does not undo a durable write.

One owned writer transaction contains all field changes, freeze markers,
events and audit. PR1 public APIs retain their signatures and self-contained
transactions; audited adapters compose uncommitted primitives instead of
looping over committing setters. Definition create/update/archive emits one
`org_config_write` row under `config:task_attributes`, with section
`task_attributes`, tiers `["org"]`, operation, definition_id, full before/after
wire definitions and actor_kind founder. Value changes emit `task_attribute_set`
or `task_attribute_cleared` under the actual task ID with definition ID/version,
revision, actor ID/kind, old/new typed presence and optional note, in input order.
The existing audit columns/scope conventions remain unchanged. Audit/event
failure rolls back every domain change, including first use. Refused operations
emit no successful domain mutation audit. Dedicated tables remain history truth.

Events include read-only `actor:{canonical_id,kind,addressable_name}`; Founder
is always actor_id/kind founder even if its current name is unavailable. No
rendered Who label, UI, filter/export, admission producer or CLI is added here.
The retained standalone command above now also executes P01–P28 route contracts,
including real two-connection orderings, SQL-insert faults, independent durable
row checks, coherent snapshots, complete 2,048-event history and full OpenAPI
model checks. This remains outside retired Python/Web units, skipped general
integration, Product E2E and required lint/static checks.


## PR3 — plain creation, CLI discovery and task values

Accepted implementation/test design: TASK-19152 `pr3-test-design.md`, 79,656
bytes, SHA-256 `e9c879ea291ed67b2592156fb77de0c6cc74d81e2ae9ebfb48c5500cc54634a3`;
TASK-19157 design PASS; manager TASK-19150 acceptance bound in TASK-19160.

POST `/tasks` has optional `attributes:{key:native_value}`. Absence and `{}`
record one positive empty admission; null and non-objects refuse. All supplied
keys must resolve to current org-local non-archived definitions. Semantic
failure returns 422 `detail:{code:"attribute_validation_failed",errors:{KEY:[REASON]}}`;
container refusal is keyed `attributes`; SQL/storage failure returns 503
`attribute_storage_unavailable`. An invalid set leaves no task/ownership/link,
attribute row, first-use marker or successful mutation audit. Existing unrelated
owner/attachment/model refusals remain in place.

`runtime/task_attribute_admission.py` is the shared PR3/PR4 creation adapter.
HTTP creators own the org async lock, move gate, DB RLock and `BEGIN IMMEDIATE`.
Synchronous manager transitions retain move-gate → DB-RLock ordering and
`BEGIN IMMEDIATE`; they add no asyncio lock or await beneath these leases.
`validate_initial_attributes` reuses PR1's read-only whole-set validator before
ID/row allocation inside that writer. Insert the task, then call
`write_initial_attributes_uncommitted` with the same raw map and caller-trusted
actor. It revalidates current definitions, invokes PR1's uncommitted initial
writer and composes PR2 `task_attribute_set` audits in input order: actual task
scope, definition ID/version, revision 1, actor ID/kind, old absent and new typed
presence. Empty/omitted fields emit no value or field audit. Caller must roll
back the entire creation on any failure, including binding, audit and COMMIT,
commit once, then enqueue. The adapter never awaits, commits, enqueues, looks up
parent values or supplies defaults. Preflight/cache results never authorize a
later writer. PR4 must validate every leg's explicit raw map before allocation
and preserve its producer's whole-operation rollback. PR3 shipped the adapter;
PR4 wires manager decision producers as described below.

The attachment wrapper accepts lazy task allocation so its writer validates
before task ID allocation and commits task, ownership, links, attributes and
both success audits jointly. Its existing caller contract otherwise remains.
`persist_root_task` opts into admission only for the plain HTTP producer;
knowledge-distill keeps its existing atomic `bind` and prior provenance.

The registered CLI producer is `happyranch run --attr KEY=VALUE` (repeatable),
with existing owner/brief/model/attachment options. `attributes list|show`
provides definitions, and `attributes get|set|clear|history` uses PR2 routes.
All verbs support explicit org/session proof and machine JSON. Numeric tokens
are finite JSON numbers with exact native integers, Boolean tokens exactly
`true`/`false`, strings literal RHS; duplicates are rejected before map collapse.
No attrs requires no discovery request. Set/clear read definition version and
current revision then send one CAS batch; clear omits `value`. No automatic
retry after conflicts or lost replies; nonzero uncertainty requires readback.
Original admission stays immutable. Existing Founder/session/owner/manager
boundaries are unchanged. See the [CLI guide](../agent-guides/web-and-cli.md#optional-task-attributes-in-the-cli-thr-390-pr3).

The existing standalone `scripts.test_task_attributes` venue owns H01–H03,
R01–R12, C01–C08 and served-schema A01/A03 additions; no retired suite, required
CI gate or Product E2E trigger is added. C09 is a separate disposable runtime
process transcript. The normalized OpenAPI snapshot pins status/parameter
inventory; full served schema owns the optional create field. Web API mirrors
are reserved for serial PR6 follow-up; PR3 makes no `web/` edit.


## PR4 — per-target manager decision attributes

Implementation follows the accepted TASK-19186 v2 design (SHA-256
`7c38a41b5a6e3be3352e55e4a3ad540edc34e210c5e6e1e5d4f5138de9a4b9ee`),
including EM R1 raw/presence-preserving parsing, R2 atomic carrier refusal and R3
implicit-first inventory and Inline Delegation Chains pointer. See the
[shipping handoff contract](../agent-guides/contracts/task-orchestration.md#manager-task-attribute-handoffs-thr-390-pr4).

NextStep, FanoutChild and ChainLeg preserve raw optional attributes until owned
admission. The existing delegate, fanout and then writers validate all maps under
BEGIN IMMEDIATE before lazy task allocation and reuse
`validate_initial_attributes`/`write_initial_attributes_uncommitted`; they own the
single final commit/whole-operation rollback. Attachment-carrying children share
the same writer. Fanout admission includes all nested saved maps and separate
empty implicit-first maps before any allocation. A child/carrier owns only its
map; first executable carrier legs are empty. No inheritance or retry carry.

New saved chains record trusted deciding-agent provenance. Future maps validate
without freezing/reserving definitions, then revalidate current definitions at
creation. Old unmarked/no-map chains retain unsupported/unknown semantics;
explicit maps missing provenance refuse. A refused normal then leg records durable
feedback with chain clear; a refused carrier leg commits feedback/reason, clear
and FAILED together, followed by existing terminal effects/outer sibling barrier.
Current ownership/cancel/exact-chain/native workflow checks remain authoritative.
PR1880 terminal readers and manager-branch barrier remain unchanged.

Focused evidence uses the existing standalone attributes command in
[local CI](../operations/local-ci.md#focused-custom-task-attributes-thr-390-pr1pr2pr3pr4).
The 25 accepted cases separate in-process production-boundary coverage from a
later independent disposable CLI/API transcript. Lint is not behavioral proof.
No new route, status or parameter is introduced; the completion decision remains
raw HTTP data, so no OpenAPI snapshot change is expected. List/export, web and
org scoring/configuration belong to later slices.
