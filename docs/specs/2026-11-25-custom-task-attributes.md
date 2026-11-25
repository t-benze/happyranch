# Custom Task Attributes — PR1 storage and validation

Status: PR1 source foundation; THR-390 / TASK-19073 / TASK-19090. No HTTP routes,
CLI, creation producers or UI consume these APIs yet. No definitions are seeded.
The source contract is `runtime/task_attributes.py`,
`runtime/infrastructure/db/task_attributes.py`, and the additive installer in
`SchemaMixin._create_tables`. This spec implements PRD v1.0 F1–F6 storage portions
and engineering notes A4/A5/A7. Authentication and task ownership checks belong
to the subsequent adapters; internal actor inputs are trusted, not authenticated
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
