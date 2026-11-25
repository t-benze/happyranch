# Custom Task Attributes v1: roots list filters and export

Status: PR5 implementation, THR-390. Source owners: roots routes in
`runtime/daemon/routes/tasks.py`, wire/encoding helpers in
`task_roots_export.py`, shared selection in
`runtime/infrastructure/db/tasks.py`, batch current projections in
`db/task_attributes.py`, grammar in `runtime/task_attribute_filters.py`,
CLI in `cli/commands/tasks.py`.

Bound design: TASK-19178, SHA256
`f4fe4754a9a92063b64bd985e8d663acabf11b1a5fff24850262e35fdbf2f30d`;
independent design QA TASK-19182 PASS; manager TASK-19177 accepted Q1–Q4.
Q1 authorizes export default20/max1000, max256 attribute columns/8MiB,
page-full/cursor continuation, unchanged list limits. Q2 assigns handwritten
web mirrors to serial PR6/PR8. Q3 authorizes a task-owned disposable runtime
job for real-process proof; unavailable proof is NOT RUN with a reason.
Q4 records pre-implementation manager acceptance. No schema, decision-model,
permission/authentication, audit, scoring or web UI change belongs to PR5.

## HTTP filter grammar

Paths have prefix `/api/v1/orgs/{slug}`. Both `GET /tasks/roots` and
`GET /tasks/roots/export` accept existing `assigned_agent`, `status`,
`block_kind`, `before`, `limit`, plus repeatable:

| Query | Decoded grammar | Selection |
| --- | --- | --- |
| attr_min | KEY=NUMBER | Current present numeric value >= bound |
| attr_max | KEY=NUMBER | Current present numeric value <= bound |
| attr_set | KEY | Current present value of any type |
| attr_not_set | KEY | No current present value (absent or cleared) |

Keys exactly match `[a-z][a-z0-9_]{0,63}`; numbers match
`-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?`, max128 ASCII
characters, finite on native JSON decoding. Integers retain precision.
Booleans, quoted tokens, NaN, overflow, whitespace, leading+ and leading
zeros refuse. Bounds may exceed definition constraints or be fractional on
integer-only attributes. Stored strings never become numbers. HTTP decoding
happens once; use a query encoder preserving occurrences and encode exponent+
as %2B. Split ranges on the first equals sign.

All predicates AND across operators, keys and existing filters, before LIMIT.
Duplicate operator/key refuses even with identical RHS. Min+max and set+range
are allowed. Not-set+set/range refuses; min>max refuses; equal bounds succeed.
Max16 distinct keys/max64 occurrences. Syntax validation precedes definition
resolution; deterministic sorted-key resolution precedes cursor lookup, even
for empty lists or missing cursors. Definition type validation precedes bound
ordering. Archived current keys remain filterable; reservations left by an
unused-definition rename are not aliases. Foreign-org/unknown keys refuse.
Filter and export values are current, never immutable initial estimates.

Filter errors: HTTP422 `{"detail":{"code":CODE,"errors":{FIELD:[REASON]}}}`.
Syntax fields identify operator/key when legal; aggregate errors use
`attribute_filters`. Definition errors identify the current key.

| Code | Reasons |
| --- | --- |
| attribute_filter_invalid | invalid_key, expected_key_equals_number, expected_finite_json_number, number_too_long, duplicate_predicate, conflicting_predicates, too_many_keys, too_many_predicates |
| attribute_filter_unknown_key | unknown_key |
| attribute_filter_not_numeric | numeric_definition_required |
| attribute_filter_inverted_range | min_exceeds_max |

List retains `{tasks:[...],next_cursor:string|null}` and full legacy row
projections (brief, severity, revisit, pause, origin, child-wait, selection).
Each row gains exactly the sorted filtered keys in `attribute_values`:
`{"effort":{"present":true,"value":4},"comment":{"present":false}}`.
No filters means `{}`. Empty string/false/0 stay present typed scalars;
absent values have no value field. No TaskRecord field is added.

Selection stays root-only by the root's own fields/values; children never
admit a root. Stored root status controls status filtering, independent of
severity derivation. Order is created_at DESC,id DESC; before uses strict
tuple comparison, irrespective of cursor membership in the filter. Missing
cursor gives empty success after filter validation. Full page cursor is its
last ID, otherwise null; a final full page may lead to an empty next page.
No list cap or new status/block-kind enum is introduced.

## Export contract

`GET /tasks/roots/export?format=json|csv`, format default JSON, returns one
page. Limit default20, integer1..1000; invalid limit returns422
`task_export_invalid`, errors.limit=[integer_1_1000]. Unknown format uses
errors.format=[expected_json_or_csv]. Repeated limit or format refuses with
the same code and [duplicate_parameter] for that field. No implicit all-pages.

JSON Content-Type application/json:

```json
{"format":"happyranch.task-roots-export.v1","columns":["task_id","brief_excerpt","status","assigned_agent","created_at","attribute:effort"],"attribute_definitions":[{"key":"effort","definition_id":"stable-id","version":1,"type":"numeric","name":"Effort","archived":false}],"tasks":[{"task_id":"TASK-1","brief_excerpt":"Work","status":"pending","assigned_agent":"worker","created_at":"2026-11-25T12:00:00+00:00","attribute_values":{"effort":{"present":true,"value":4}}}],"limit":20,"page_full":false,"next_cursor":null}
```

Fixed columns stay in that order. brief_excerpt is Python brief[:200] Unicode
code points, including whitespace/newlines/NUL, without ellipsis. There is no
title column in tasks. Agent is stored ID/null; created_at preserves stored ISO.
Append every current non-archived definition plus every filtered definition
(including archives), sorted once by current ASCII key. Headers are
`attribute:KEY`, never display names; metadata has the same ordering. Every
export row has every selected key with list's presence shape. Empty exports
retain schema/header; attributes created after a task have absent values.

Atomic caps: max256 attribute columns, max8MiB encoded body including
header/metadata/envelope. Overflow returns413 `task_export_too_large`,
errors.export=[too_many_attribute_columns|encoded_body_too_large].
No silent omission, partial attachment or streamed successful prefix.
List and export use ONE `_build_roots_query`; only two fixed projections
differ. Metadata selection uses a257 sentinel; compact rows retain only
200-code-point excerpts; values are batch-read (<=200 task IDs for list,
<=8 for export, <=256 definition IDs) and export batches are encoded/discarded.
There is no 1000×256 retained scalar matrix or full-brief export collection.

Both formats have Content-Disposition attachment with safe quoted filename
and RFC5987 filename*: task-roots-SLUG-YYYYMMDDTHHMMSSZ.json|csv.
SLUG replaces characters outside [A-Za-z0-9_-] with _, max64, fallback org;
timestamp is UTC at request start. Cache-Control remains no-store.
X-HappyRanch-Export-Limit is requested size; X-HappyRanch-Page-Full is true
iff row count equals limit; conditional X-HappyRanch-Next-Cursor is last ID
on full pages. This is a heuristic, not a count or promise of more matches.
Clients may repeat the exact filters with before=cursor.

CSV Content-Type text/csv; charset=utf-8, UTF-8 without BOM, stdlib csv.writer,
minimal RFC4180 quoting, doubled embedded quotes and CRLF records. Header
columns match JSON; there are no metadata/footer rows. Numeric cells use
native text, booleans true/false, absent/null agent empty. Present empty
string and absence share empty CSV cells; JSON distinguishes them.
After conversion EVERY cell beginning =,+,-,@,TAB or CR gets one apostrophe
before CSV quoting. Numeric -5 therefore becomes '-5. Empty cells and leading
space/apostrophe remain unchanged. JSON values are never neutralized.

## Snapshot, read authority and failure

Each request encloses definition resolution/metadata, cursor, selection,
list-derived DB projections and current values in
`org.db._memory_collection_read_budget()` plus
`org.db.coherent_read_view()`. Encoding finishes within that view and a
detached bounded body leaves it before network delivery. Independent WAL
commits cannot mix task fields, selected columns/metadata and values.
Caller-owned transactions remain caller-owned. Separate pages have separate
snapshots. Existing correlated indexes support ID lookups, not indexed numeric
range seeks; native scalar JSON comparison uses a synchronized deterministic
SQLite function to avoid arbitrary-integer rounding/binding overflow.

Router bearer and OrgDep are reused unchanged. Session proof grants no extra
access. Existing401/404/409 and read-budget503 remain inherited; attribute
SQLite/store failures are whole-request503 attribute_storage_unavailable.
GET never writes task/value/history/audit/config or enqueues work.

## CLI and verification

See the [CLI guide](../agent-guides/web-and-cli.md#roots-attribute-filters-and-export-thr-390-pr5)
for exact flags/examples, conflicts, stdout/file bytes and stderr continuation.
Plain tasks remains compatible; roots --json follows existing JSON-array
convention; roots --all-pages is traversal, never fixed-snapshot export.

Supported focused venue:
`uv run --frozen python -B -m unittest scripts.test_task_attributes -v`.
Accepted case IDs L01–L16 cover grammar/precision/presence/composition/cursors/
legacy projections/current/archive rules. E01–E11 cover parity/schema/excerpts,
two-gate/two-connection snapshots, caps/continuation, bytes and headers.
A01–A03 cover inherited authority, persistent read-only effects and whole-read
failures/recovery. C01–C05 cover shipping parser/real-ASGI transport, repeatable
forwarding, local conflicts, byte output and failure file preservation.
S01 observes served full schema/classification; normalized OpenAPI snapshot
alone pins only parameter names/statuses.

Regenerate via `uv run --frozen python scripts/generate_openapi_snapshot.py --write`,
then `--check`. T01 is separate real CLI/API process proof against a
task-owned disposable runtime job with exact source SHA, private port/PID and
teardown evidence. In-process bridges cannot establish T01. Long verification
uses durable jobs and actual terminal receipts. Required hosted checks remain
Source Boundary Lint and Web Lint (Node24); retired suites and Product E2E are
NOT RUN, general integration SKIPPED under THR-243. Independent exact-head
review/QA and manager merge remain required.

Residual client gap: PR5 makes no web edits; serial PR6/PR8 own handwritten
roots filter/attribute_values/export helper parity and filter/export UI. No TS
generator exists. Source/API reachability is additive; native enforcement is
unchanged, and inventory classification is not an authorization grant.
