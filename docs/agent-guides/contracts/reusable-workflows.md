# Reusable workflow contracts

Founder THR-336 message 85 approved TASK-14461 plan revision 3 under manager
root TASK-14478. TASK-14483 delivered **U1a**, the records/validator half of U1.
TASK-14509 adds **U1b**, the common pure reducer and data-driven brief renderer, for
F1/F3/F6 and AC1/AC5/AC8. These are source interfaces, not M1 acceptance.

`runtime/workflows/sequential_contracts.py` owns closed syntax and immutable
compiled records. `sequential_definition.py` owns pure canonical parsing,
definition compilation, graph validation and start completeness checks.
The reducer and brief renderer build on these modules. None allocates tasks,
reads resources, registers releases, performs I/O, calls Coding progression, or has a production producer caller. The
definition identity is a pin, never a progression or brief-builder selector.

## Definition and ABI

The new family is `workflow-sequential-definition@1`, distinct from historical
document compilers and `workflow-coding-definition@1`. Definition fields are
`format`, `identity`, positive INTEGER `version`, `description`,
`required_inputs`, `roles`, `entry_node`, `nodes`, `limits`, and
`terminal_conditions`. Node fields are `id`, `role`, `responsibilities`,
`inputs`, `output_contract`, `completion_checks`, and `outcomes`.
Roles declare executable agent requirements, worker/registered-manager
eligibility and independent role pairs. Principals and repository authority
are not definition fields.

`compile_definition(raw, source_digest=...)` receives exact canonical UTF8 JSON
and an independently selected immutable source/release SHA256 pin. It returns
`CompiledDefinition(raw, pin, definition)`. The pin fixes identity/version,
definition SHA256, source SHA256, and these exact ABI identifiers:

| Pin field | Identifier |
| --- | --- |
| compiler | `workflow-sequential-compiler@1` |
| validator | `workflow-sequential-validator@1` |
| interpreter | `workflow-sequential-interpreter@1` |
| capabilities | `workflow-sequential-capabilities@1` |

The interpreter pin names the pure U1 reducer interface; the capability pin
reserves trusted later-unit integration. Neither advertises an installed engine
or working capability implementations.
U1a's registered contract/check vocabulary is a finite syntax registry only.
No caller may treat a manually constructed `CompiledDefinition` as admission
proof. A future registration/reader boundary must recompile the pinned bytes
and compare the independently selected source/ABI pins.

Canonical encoding is sorted keys, compact separators, literal UTF8
(`ensure_ascii=False`), with no final newline. Saved bytes must already equal
that encoding. Unknown fields/families, duplicate JSON keys, malformed UTF8,
nonfinite/floating-point numbers, Boolean integers, excessive nesting, unknown
checks and unsupported versions refuse. Parsing never normalizes saved bytes.
Maps in the compiled view become immutable ordered key/value tuples and all
arrays become tuples; frozen nested models retain the original raw byte pin.
Serialize wire records from their specified JSON shapes, not the compiled
definition's tuple-based `model_dump()` representation.

## Finite graph rules

The ceiling is 32 nodes, 16 roles, 32 input/output slots, 8 outcomes per node,
64 visits, 3 content epochs, 2 content returns and 4096 events. Definitions are
at most 1 MiB; result envelopes at most 64 KiB; JSON nesting at most 24.
Text is at most 16 KiB UTF8 per field, nonblank and NUL-free. These are
cumulative family bounds, not an extra allocation or execution allowance.
Node outputs: `immutable-document@1` at most 1 MiB (1,048,576 bytes); every
JSON-contract output at most 48 KiB (49,152 bytes) of canonical bytes. The
engine checks raw length before any write, decode or digest. Larger output
refuses with `sequential_output_limit_exceeded`, is never saved, and cannot
succeed or advance (Founder THR-336 msg102, Decision 1 = B).

Every node has a unique ID/output slot, an existing role, nonempty declared
responsibilities, named inputs, a registered typed output and exact compatible
completion checks. Outcomes are finite and contract-specific:

| Output | Success | Other declared outcomes | Required completion checks |
| --- | --- | --- | --- |
| `immutable-document@1` | produced | BLOCK | immutable-document@1 |
| `code-change@1` | produced | BLOCK | repository-pre-edit@1, code-change@1 |
| `document-review@1` | APPROVE | REQUEST_CHANGES, BLOCK | independent-document-review@1 |
| `independent-code-review@1` | APPROVE | REQUEST_CHANGES, BLOCK | independent-code-review@1 |
| `code-qa@1` | PASS | REVISE, BLOCK | code-qa@1 |
| `guarded-repository-merge@1` | merged | held, uncertain | guarded-repository-merge@1 |

Each outcome selects exactly one closed edge: `next` with `node`, `hold` with
`owner=accountable_manager` and `remedy=no_automatic_retry`, `fail` with a
bounded `code`, or `terminal` with a declared `condition`. A next edge for
REQUEST_CHANGES/REVISE must consume `content_returns` and explicitly set
`invalidate_dependents=true`. Other next edges cannot consume that counter.
Omitted next-edge return fields mean no counter and no invalidation.
BLOCK/held/uncertain cannot advance or satisfy a terminal condition.

All successors and role independence references must exist. All nodes must be
reachable from entry. Removing counter-consuming edges must leave an acyclic
graph, so every possible cycle consumes the finite counter. At least one
terminal path must fit the definition's own visit/return/epoch limits.

Sources are `run.NAME`, `current.SLOT`, or optional `returned_findings`.
`run.request` is required. A required node input cannot reference an optional
run input. Every required current slot needs a dominating producer on **every**
path. The finite intersection analysis retires the returned producer and every
control/data-dependent selection. Unaffected prerequisites remain available,
so a return need not target entry. Retired output references cannot satisfy it.
Terminal outputs must be available, with the producer's registered typed
witness plus `native-success-and-positive-outer-settlement@1`. Static validation
establishes possible typed evidence, never actual successful settlement.

Unused roles remain valid: the reviewed develop→finalize fixture deliberately
retains four role requirements while omitting review/QA producers. It has no
dangling inputs. Its future trusted merge capability must still independently
require current exact-subject review and QA, authority, native settlement and
guarded action evidence. Earlier graph refusal is not proof of that policy.

## Closed command, result and transition syntax

`parse_start` accepts only canonical `workflow-sequential-start@1` with
`operation_key`, full `definition` pin, `settings` generation/snapshot digest,
`request`, typed `inputs`, complete `bindings` (role/permanent principal), and
`lower_limits`. Request is supplied once, outside the other input array.
There are no repository/base/head/path, source task/SID/result, authority or
task-allocation fields. Binding claims do not prove current eligibility,
team membership, permission or the initiating manager's routing authority.

`validate_start` checks the exact compiled pin, complete roles, declared
independence and required typed inputs. `effective_limits` intersects the
definition, original source limits and requested lower limits, coupling epoch
and return ceilings conservatively. These helpers have no current-authority,
settings-admission, ownership, native or resource observation. Future admission
must supply those proofs and refuse projected work that cannot fit lower
remaining budgets. A new ID, wait or replay never replenishes an allowance.

`parse_result` accepts canonical `workflow-sequential-result@1`: run/visit IDs,
definition/input-selection digests, bounded outcome token, output references,
attributed findings and capability receipt references. Output refs fix slot,
typed contract, epoch, producer visit/task/SID/positive INTEGER result and
digest. produced/APPROVE/PASS/merged require outputs. Other outcomes require
attributed findings and carry no successful output claim. Duplicate output
slots/capability checks refuse. Unknown bounded outcome tokens require attributed
findings and reach the reducer as a hold; definition outcomes remain the closed
finite registry. A reference or unsigned report is no trusted
receipt; syntax parsing does not validate content, provenance, selected visit,
current subject, settlement, authority, or whether the node permits the outcome.

`parse_transition` accepts canonical `workflow-sequential-transition@1`: run
and definition pin, source visit/INTEGER result and result/output digests,
node/outcome, prior CAS/event digest, before/after counters, invalidated output
refs, selected closed edge and successor visit/task or null. A non-registry
bounded outcome token is accepted only with a hold edge; next, terminal and
fail edges still require the existing outcome registry. This matches the
result/reducer unknown-outcome hold without relabeling saved result bytes.
A next edge needs
its matching successor; other edges cannot supply one. Each counter snapshot
requires `epoch=content_returns+1`. The pure reducer determines counter deltas,
selected edge, invalidation and result identity; a future owning writer
must authenticate persisted history, CAS, one-time consumption and genuine
fresh task/visit allocation. Parsing grants none of those effects.

## Pure reducer ABI

`sequential_reducer.reduce_result(compiled, state, result) -> Decision` uses
`workflow-sequential-interpreter@1`. All records inherit the frozen, strict,
extra-forbidden U1a `Closed` model; collections are bounded immutable tuples.
The function has no allocator, clock, environment, filesystem, network or DB.
It looks up nodes/declared edges from the compiled data without preset dispatch.

- `ActiveVisit`: node, visit/task/session IDs, epoch, input-selection digest.
- `RunState`: run/definition digest, immutable original and requested lower
  limits, counters, active visit (or null), current output selections, cumulative
  attributed findings, and previously committed `Decision` receipts.
- `AuthenticatedResult`: genuine task/session/INTEGER result binding, parsed
  envelope and distinct `verified_checks`. This is a **trusted adapter assertion**,
  not a public callback model or a proof producer. The adapter must authenticate
  the complete callback, current authority, exact domain subject, output integrity,
  access and positive original native/outer settlement. Unsigned report/check
  references cannot construct that trust.
- `Decision`: run/definition/source/result identity, outcome, result digest,
  before/after counters, edge, categorical reason, optional `Activation`, current
  selections, invalidated references and cumulative findings. `Activation` holds
  only node, epoch and selected current inputs; it has no allocated IDs.

The result digest is SHA256 of canonical JSON for the entire authenticated
assertion, including binding and verified checks. Any changed field conflicts.
A committed receipt matching the visit/result returns the exact earlier decision,
even after later progress; reuse of either identity with changed content refuses.
Foreign run/pin/visit/task/session/input digest or finding attribution refuses.
The future U2/U3 complete reader/writer must authenticate receipt history, fixed
limits, current selections, CAS anchors and actual IDs; constructing these Python
models establishes no persisted-history validity or current authority.

Only a declared outcome selects an edge. Unknown/undeclared outcomes hold with
zero activation. Successful output must match the single declared slot/type,
current epoch and exact source visit/task/session/result; declared completion
receipt references and trusted verified checks must match. Positive native/outer
settlement is required even for refusals/returns. Missing evidence holds without
selecting successful output. Terminal selection requires its declared output to
be present among validated selections; trusted domain policy remains the adapter's
responsibility. The develop→finalize fixture uses the same reducer and reaches
that future capability boundary; synthetic merged input is no merge-guard proof.

`effective_run_limits` returns effective **ceilings**, intersecting definition,
original and lower requested limits. Accepted reduction spends one event;
a successor intent spends one visit. A declared return retires the target
producer and every control/data-dependent selection using U1a's shared return
analysis, appends attributed findings, and increments epoch/return counters
before a fresh producer intent. Unaffected earlier-epoch prerequisites remain.
No wait, replay or new ID resets limits. At exhausted event/visit/return/epoch
limits the decision holds with zero activation. No counter exceeds its ceiling;
an attempted return after the last permitted epoch still retires stale dependent
selections but cannot spend another return/epoch. Missing required current input
also holds without reserving a visit. Effects and history remain external.

## Pure brief ABI

`sequential_briefs.render_node_brief(compiled, state, original_request=...,
inputs=BriefInputs(...)) -> NodeBrief` renders `workflow-sequential-node-brief@1`.
The trusted caller supplies the immutable admitted original request and already
authorized bounded disclosures. `RecipientInput` names a source, recipient roles,
text and (for current output) its exact selected reference. A role/reference
confers no access. Only declared inputs addressed to the pinned node role render;
a filtered required input refuses without disclosing its bytes. Current references
must equal the selected output, and findings render only for a node declaring
`returned_findings`. Original findings retain visit/INTEGER-result attribution.

The brief includes pinned responsibilities, output contract, completion checks,
outcomes, original request, filtered inputs and remaining counts under effective
ceilings. It returns fixed `system_text` separately from JSON `node_data`; adapters
must install these at their corresponding trust levels and add trusted domain
supplements. Definition/data text cannot replace the fixed system safety field,
change policy or introduce scripts/expressions/permissions. Rendering grants no
allocation, execution, disclosure or repository authority.

## Bundled data and acceptance status

[Example provenance and finite acceptance cases](reusable-workflows-examples/README.md)
record the four unchanged reviewed example files, including their presentation
newline. Source file digests include that newline; canonical definition digests
exclude it, as the reviewed examples specify. A future resource reader must
explicitly extract canonical content before compilation; the compiler never
strips or normalizes saved bytes. Coding release v2 is separate
bundled data at `runtime/workflows/builtin_templates/coding.v2.json`, explicitly
included in wheel packaging. U9a adds the package reader/selector below; inert
U9b setup adds explicit offline registration; atomic U9b now attaches reviewed-v2
public Coding selection and old-fresh refusal together. The three
other files live only in documentation as **noncatalog** examples.

Historical `templates.py`, `release_templates.py`, `coding.v1.json` and
`coding_progression.py` remain byte-identical. U1 changed no storage/schema,
task producer, route, settings, admission, CLI, native enforcement or old-reader
behavior. U2a's separate storage references are described below. Existing
admitted histories keep their original family. Startup, installation, cutover
and live enablement are outside U1 source delivery.

No committed tests or behavioral harness are added. Python units remain RETIRED,
general integration SKIPPED under THR-243 seq42, and Product E2E is not triggered.
The maker's authorized out-of-tree pure-function smoke covers all four examples,
forward paths/briefs, review and second_read returns, QA REVISE, original lower
limits and third-epoch exhaustion, selection invalidation, unknown/undeclared
outcomes, identical/conflicting replay, recipient filtering and unverified
settlement. These synthetic adapter assertions prove no actual authentication,
capability, native settlement, allocation or guarded merge. Actual service
progression, negative compiler coverage, real-task returns and M1/V3 acceptance
remain NOT RUN/open. Lint/static results are source evidence only. Independent
full review and scoped QA precede manager guarded merge.

## U2a storage and standalone reader boundary

TASK-14536 under replacement root TASK-14531 originally added the records and
standalone-reader boundary of U2a without a production caller, producer,
allocator, route or startup SQL. U2b originally added only the explicit operator
script described below; the bounded startup composition is described below.
These source units do not close U2,
M1, AC5, AC8 or AC12. TASK-14576 under root TASK-14563 adds the
readers-only composition below; TASK-14597 adds U2b installer/check source.
Real installation and U3 producers remain separate.

`runtime/infrastructure/workflow_sequential_schema.py` owns a separate version1
marker and nine tables: definitions, settings snapshots, settings operations,
runs, visits, outputs, evidence, events and outbox. Definitions, settings,
operations, outputs, evidence, events and notifications are immutable, with
UPDATE/DELETE refusal triggers. Runs retain immutable admission pins; visits
retain immutable node/task/input pins and an accepted result cannot be replaced.
Current lifecycle fields remain with their future writer. All result references
are genuine positive INTEGER native identities, never operation IDs. Unique
org/initiating-principal/start-key and source/result/start constraints retain
the exact original source task/session/INTEGER-result and canonical request
binding. A new source task/result cannot reuse the same principal/key to create
another run; the complete reader also refuses cross-run start-key conflicts.
Visit ordinal/predecessor and accepted source visit/result constraints select
one operation; a partial unique index permits one selected visit per run. The complete reader additionally checks the active pointer and
rejects committed provisional runs. Only the new run `last_event_id` FK is
`DEFERRABLE INITIALLY DEFERRED`. U3b's caller-owned `BEGIN IMMEDIATE` keeps
foreign keys enabled and sets transaction-local `PRAGMA defer_foreign_keys=ON`
for the run/native-task/visit cycle. It inserts the provisional run with a null
active pointer, then the visit/task/mark and event, and finalizes pointers.
Selected admission validation precedes COMMIT; SQLite checks the deferred FKs
at COMMIT, and missing references refuse. Deferral resets at COMMIT/ROLLBACK. No existing
table or transaction/startup owner is changed. No table extends frozen
document/Coding state grammar. No index is placed in an existing startup executescript block.

`runtime/workflows/sequential_history.py` compares the whole owned schema with
independently constructed F/E/G/H/I/J references, Coding absent/present, owner
indexes absent/complete and marks absent/v1/v2. It does not filter new objects
out of the candidate, derive references from candidate SQL, mutate the supplied
DB or relax an existing reader. Partial/extra/changed objects refuse, including
an otherwise empty extension. `read_sequential_history` validates all new rows:
canonical bytes/digests and ABI pins, settings generations and operation custody,
native task/result tuple links, visit predecessors/selected inputs, original
limits, reducer decisions, event sequence/hash/CAS anchors and exact task outbox
closure. Its transaction joins the caller's snapshot or rolls back its own read
interval. Empty/absent new storage says `new-engine unavailable`. A populated
`storage readable` inventory establishes new-record consistency only, never
engine readiness, business admission, current authority or authentic native
settlement. Stored verified-check arrays remain adapter assertions for U3 to
authenticate; document-only content/capability policy is supplied by U3f below,
while repository policy remains with U4/U5 remainder.

The sequential acquisition path uses the retained
`coding_authority_history._Budget` / `_bounded_rows` / `_decoded` model and
`workflow_schema._candidate_structure` / `_structure_cells` metadata acquisition.
SQL `typeof`/byte-length gates withhold wrong-class or oversized cells before
payload fetch. Decision 2 (THR336 msg102; accepted TASK-14643 rev4) separates
observed schema/native metadata (A), independent reference metadata/comparison (B),
and sequential data, native links, decoded/model copies and per-run replay (C). Each sequential table is acquired once; visits/events
are grouped once instead of rescanning all history for every run. Ownership acquisition is scoped to authenticated run-bound tasks: cached run/visit
relations produce bindings, with primary-key native-task and mark lookups under C.
No binding-view text is acquired inside C. Zero-row type/size/NUL guards and
rowid-table decode-candidate checks preserve the non-run guarantees under L's
org-wide SQL meter; candidate rows/bytes/steps consume C. The unchanged retained
legacy `_inventory` traverses and decodes the complete binding view under L,
including cross-family conflicts.
The complete layout match also returns the already acquired column metadata to
sequential row acquisition inside that same snapshot. It retains the exact
validated tuples and charges the metadata map to C;
row acquisition does not repeat `pragma_table_xinfo`. Full native metadata,
independent comparisons, exact-type gates and every existing cap remain intact.

A, B and C each permit at most 8192 acquired rows, 64 MiB raw/decoded/copy
reservations, 65536 traversal/replay steps and two million SQLite progress units.
L permits 32 million SQL progress units only; its acquired rows/bytes/steps
consume C. All four share one original cooperative five-second deadline and
accumulate without reset between phases or runs. A is re-entered for native
metadata. Progress handlers are sequential, never nested. Fixed source-literal
reference construction before the helper yields is unmetered SQL, bracketed only
by B's deadline checks. Individual cells remain at most 1 MiB; existing
definition/result limits also apply. Large hostile schema acquisition exhausts A;
small extra workflow objects still refuse at the complete comparison under B.
Callers must exclusively own the connection and its progress hook during the
call. Checks occur before success, including unavailable/empty results;
exhaustion raises a refusal, never validates a subset or reports empty success.
These are cooperative limits, not hard RSS or OS-I/O timing guarantees.

Legacy-only and combined complete validation receive the unchanged native
connection and an exact retained `_Budget` with its original per-call caps.
TASK-14576 separates those narrower counters from the new reader's schema/copy
charges: each retained row/byte/work counter is added back to C on success or
refusal, and C's byte/work/deadline checks run before success. L meters retained
SQL under the same original deadline. No allowance replenishes. Seeding the wider new-reader metadata
charges
into the original 16 MiB/16384-step counters would reject even empty Coding.
Retained readers may refuse at their own original caps. Their acquisition is
bounded by those caps plus L's SQL meter and the shared deadline, **not
pre-reservation against C's 64 MiB cap**. Retained document/ownership queries
without a budget argument remain under L's progress handler; their bytes are
not added to C. Original refusals propagate unchanged.

The separate `workflow_sequential_ownership.py` companion reference keeps all
original columns and v1 kinds, and adds explicit v2 `seq_source_waiting`,
`seq_carrier`, `seq_visit` marks. The literal view appends three relations to
the original branches unchanged. Run-scoped validation rejects missing/conflicting
sequential marks under C. Retained legacy completeness under L rejects non-run
wrong/missing/stray marks and bindings to missing tasks in the same call, with
`workflow_task_ownership_invalid:<t>`, `workflow_task_ownership_incomplete`,
`workflow_seq_ownership_relation_conflict` or
`workflow_task_ownership_task_missing:<t>` respectively.
Cross-family conflicts still refuse, though an earlier retained family validator
may supply the first code. A selected v1 mark still uses the existing scalar
reader. There is no
source adoption or native task encoding change. The explicit U2b installer below
rebuilds this companion while preserving every v1 mark; adoption belongs to U3.

**Complete reader composition (TASK-14576):** the standalone reader first
validates the entire sequential structure and data/ownership closure. It then
enters a connection-bound read scope for the original complete document/Coding
validators, whose recursive structure checks compare independently built complete
combined references. Shared installed readers outside this scope still refuse
the extension. The selected startup consumers below explicitly enter this
complete read; there is no global recognition. Genuine native connections and
exact retained budgets keep their guards.

The Coding discriminator imports the closed `SEQUENTIAL_TABLES` names from their
DDL owner. Only within the validated scope, it acquires and compares each enrolled
row in full, with exact storage types, against the sequential reader's authenticated
same-snapshot rows. Thus the separate Coding definition's scalar identity and
identity inside `definition_bytes` belong to the complete sequential reader.
The scan is retained; no prefix exemption, table hiding, metadata sanitizer,
connection/budget adapter or non-sequential refusal change is introduced.
Partial/lookalike/unknown structures refuse, and non-sequential reserved metadata
still requires its original Coding family. Definitions, settings and empty storage
do not imply engine availability or legacy fallback.

Installed selected ownership recognition preserves v1 results. U3a exposes a
consistent v2 source/carrier/visit kind only with an attached same-org holding
`SequentialDispatcher`; otherwise classification returns `reconciliation_required`.
Both selected scalar readers return the exact sequential kind.
Selected lookup uses the mark/binding index and scalar run/visit/native relations,
not a full-history scan. Complete inventory remains at the compatibility boundary.
No install/reopen/backout result or compatibility safety claim follows
from this reader boundary. [Installation/backout contract](../../runbooks/sequential-workflow-storage.md)
is UNVERIFIED. V3 and actual runs remain NOT RUN; old bytes and historical
receipts remain unchanged. U4–U9 remain held on the separate U4.0 NO-GO.


## U2b explicit installer/check source

`scripts/install_workflow_sequential_schema.py` is the sole caller of
`workflow_schema.install_sequential_schema`; no daemon/startup/org-init
path calls it. Both check and install first use the existing complete scoped
reader on an immutable RO checkpointed image. Nonempty WAL/journal files refuse;
file bookends detect observed drift. Exit0 means compatible/installed storage,
exit3 means install-needed, exit1 refusal, exit2 usage and exit4 uncertainty.

New installation requires a complete original v1 ownership companion, maintained
cooperative offline/native-owner exclusion, registered stopped lifecycle and a
separate checksum-verified backup matching the entire source SQL/typed-row/rowid
inventory. One native `BEGIN IMMEDIATE` with foreign keys enabled installs the
unchanged literal extension and marker, rebuilds only ownership table/view to v2
and copies all original v1 values/rowids. Complete independent target/data/FK
validation and preservation comparison precede COMMIT. No ordinary-source
adoption, old-layout upgrade, startup initialization or guard change occurs.
Known mismatch rolls back with error evidence; failed COMMIT stays uncertain,
never success. Installed complete storage reruns are RO no-ops. Missing marks or
partial extension refuses; there is no repair/backfill mode.

The separate preservation inventory is bounded to 8192 rows, 64 MiB encoded cells
and five cooperative seconds per observation; unsupported rowid/virtual/generated
shapes refuse. Original complete-reader caps and refusals remain intact. File and
lifecycle checks are cooperative observations, not an OS fence or native closure
proof. Even empty installed ownership@2/storage requires compatible code;
old-code-only downgrade is unsupported and must refuse. See the
[runbook](../../runbooks/sequential-workflow-storage.md) for backup, custody and
backout limits. Execution behavior remains **UNVERIFIED**: disposable structural
SQLite observations do not establish real install/reopen/backout safety, V3,
current authority, native settlement or ordinary-use acceptance. U3–U9 remain
outside this source unit.


## Bounded U2/U3 startup composition (F-M1-REOPEN)

ROOT TASK-14640's three F-M1-REOPEN rulings and accepted TASK-15007 design rev3
select only the org-load schema wrappers, cutover reads, final task-ownership
installation read, naming installation target admission and builtin registration
source/changed-target reads. `OrgState.load` retains its original order and
failure-close owner. `install_or_recover` and shared legacy reader internals
remain unchanged. Later Coding/document operational consumers gain no recognition.

`workflow_schema_load.read_org_layout` examines every main/TEMP object's name
and owner, independent of kind, under a bounded selector. A casefolded
`workflow_seq_` name or owner selects the existing complete sequential reader
in that SAME snapshot. Partial, wrong-case/kind, TEMP-shadow, foreign and corrupt
storage refuse; no fallback follows a selected sequential failure. The complete
reader returns its genuine retained `(document_layout, coding_inventory)` tuple
additively with all previous `SequentialHistory` fields. Its authenticated-row
context is confined to one complete read and reset on every exit. Historical
direct absent-storage callers retain their unavailable early return.

Complete real-installer J with an I-origin active Draft/Review workload and J
with Coding schema only use this path. Literal I plus the extension without
Coding refuses with `ValueError('workflow_schema_object_set_mismatch')` through
the reopened source read, before automatic Coding DDL, conversion or binding
refresh. The original transaction rolls back and unpublished load closes its DB.
I is never relabeled J. Operator remediation is to restore the verified
pre-install backup, boot normally once, stop, then use the reviewed installer
under its original prerequisites. No new command or live operation is authorized;
installer-side literal-I refusal remains separate follow-up F-M1-I-INSTALL.

With no reserved sequential object, the selector ends only its own read
snapshot before invoking the exact original expression with its original
arguments. Legacy values, bytes, errors, transaction exits and source-reuse
eligibility remain with that owner; the extra read-only selector SQL is
observable. Sequential results bypass source reuse. Writer targets always read
fresh after mutation, on the actual original cumulative retained budget.
Retained counter deltas are charged once to C, including refusals. Advances
remain provisional until the complete read and snapshot exit succeed; an outer
writer still owns publication after its own successful exit.

Selector and A/B/C/L share the original absolute five-second deadline within
each read; the selector has its own unchanged finite allowance. Narrow retained
caps, scope separation and source/target budget identity remain unchanged.
ROOT's observation thresholds are A rows <=4608 with SAME-schema/history
invariance and no exhaustion; A bytes/steps/SQL/deadline, B/C/L, selector and
narrow retained dimensions each retain their own 50% half-cap. Historical
TASK-15007 structural observations include A=4158 on literal I (4034 rows of
8192 headroom), J=4452/4460/4492/4502 at distinct startup schema stages
(3740/3732/3700/3690 headroom), and absent-J=3736/3786 (4456/4406 headroom).
Same-schema active-document history and builtin source/target each observed
4502 ->4502. Schema growth consumes A. These are attributed design-source
observations, not candidate-head capacity, behavior or V3 proof.

This source composition performs no startup sequential installation or upgrade,
live migration, venue/M1 execution, deploy, restart or activation. Empty installed
storage still requires compatible code; no old-reader downgrade is supported.
Independent full review and same-head scoped structural QA remain required.
Only Source Boundary Lint and Web Lint (Node 24) are automated merge checks;
Python units remain RETIRED, integration SKIPPED THR-243 seq42, and candidate
behavior, native closure, backup/reopen/backout and V3 acceptance remain unproven.


## U3s venue-only sequential start enablement (SB-1 a)

Founder THR-336 msg102 accepts SB-1 (a): an operator-only disposable-venue
publisher and a SQL-free marker fence, both inert until U3e attaches them.
This source adds no runtime caller, route, CLI verb, UI, credential, environment
flag or settings/config field. Coding settings/start and the U9 fence are unchanged.

`scripts/setup_sequential_venue.py` requires `--runtime-root`, `--org`,
`--definition`, `--definition-sha256`, `--bindings-file`, `--admission`
(`Allowed` or `Disabled`), `--operation-key`, `--principal` (operator label),
and `--confirm-disposable-venue`. `--limits-file` optionally lowers the definition
limits; otherwise those exact limits are used. Bindings are a closed JSON array
of `{role,principal}` objects containing exactly the declared roles and satisfying
all declared independence pairs. Limits are the closed `Limits` object.
These labels/bindings grant no authority; actual admission remains with the
unchanged authority coordinator and engine.

**V1 (engineering_manager TASK-14640 step9):** U3s structural observations use
an own-UID, mode-0700 disposable `mktemp -d /var/tmp/hr-u3s-<task-id>-XXXXXX`
directory whose realpath equals its literal path and lies outside the passwd
home entirely. It contains only fixture runtime/daemon-home trees, initialized
org DBs and backups. No daemon, executor or job starts there; no live-home
content is copied. The live-home guard stays unchanged, with no symlink, mount,
UID, HOME or environment override of that guard. Save concise evidence, then
remove only that exact directory if safe and record its path/removal exit.
This grants no V3, V3-0b or genuine Coding scratch run. A future V3 venue must
also lie outside the real account's `.happyranch` tree.

**A1' (engineering_manager TASK-14640 step8):** this script is the bounded,
explicit resource reader. It reads a regular no-follow file of at most 1 MiB,
requires `--definition-sha256` to equal SHA256 of the complete source file,
and requires exactly one final LF (no missing/double LF or CRLF ending).
Canonical bytes are exactly `raw[:-1]`; no other stripping, normalization or
reserialization occurs. The unchanged compiler refuses noncanonical bytes.
Optional `--canonical-sha256` must equal SHA256 of those canonical bytes.
The script prints `canonical_sha256` with the original storage receipt.
Every mismatch refuses with exit 1 and zero new rows. A2: `StorageSettings`
and `_bytes` are imported read-only from the unchanged history owner.

The existing sequential storage must already be installed through the explicit
offline installer. The publisher refuses agent task environment markers,
live-account daemon/org paths, unlisted/aliased runtime roots, unstopped daemon
controls, invalid definitions/bindings/independence, and incompatible storage.
The live-account home is obtained from passwd, independently of private `HOME`.
The operator maintains cooperative stopped-state exclusion; neither missing PID
nor the confirmation flag proves historical cleanup or excludes same-UID writers.

One `BEGIN IMMEDIATE` publishes an immutable definition, the next contiguous
settings generation and its exact request/receipt operation row. Deterministic
IDs bind org, operator and key; exact replay returns the original receipt with
zero rows; a different request under the same identity refuses. A directory
advisory lock serializes operator invocations through oracle/marker publication,
without creating a lock file. Before changing a generation the publisher
withdraws any previous marker. Allowed ordering is COMMIT, then the unchanged
`read_sequential_history` oracle (`storage readable` required), then marker.
Disabled ordering is marker unlink before the Disabled generation. A post-commit
refusal preserves committed rows and reports them; it never repairs history.
An old operation replay never changes the current marker decision. Exits are
0 success, 1 refusal (including post-commit refusal), 2 argument usage.

`runtime/workflows/sequential_venue.py` reads `HAPPYRANCH_DAEMON_HOME` fresh.
Metadata-only environment/live-home checks precede every file open. Live aliases
receive `sequential_venue_live_home`; other missing/relative/aliased homes receive
`sequential_venue_env_absent`. Its closed no-follow marker is
`sequential-venue-start.json`, format `happyranch-sequential-venue-start@1`:
exact keys `format`, `daemon_home`, `runtime_root`, `org_slug`, `database_dev`,
`database_ino`. It must be a regular, single-link, current-UID file of at most
4096 bytes, strict UTF8 JSON without duplicate keys/constants. Paths, org and
DB inode must match. Refusals are `sequential_venue_marker_absent`,
`sequential_venue_marker_invalid` or `sequential_venue_mismatch`. The helpers
publish via an exclusive pending inode and descriptor-relative rename/unlink.
This is a fresh filesystem observation, not a racing-rename or same-UID fence.

U3e alone will attach the fence outside writers at the completion pre-check and
again before capture/admission. No callback or continuation consults the fence.
Marker removal disables the next fresh start; a newer Disabled generation makes
older settings fail `settings_generation_stale`. The latter may also hold U3c
continuations because its selected reader checks newest-generation pins; use
marker removal alone for the V3 mid-run scenario. V3, V3-0b and U3e attachment
remain UNVERIFIED. See the [U3s record](../../superpowers/specs/2026-11-08-sequential-venue-start.md)
for the finite matrix, future attachment and teardown contracts, and I1–I4.

## U3a ownership union and holding attachment

TASK-14616 under ROOT TASK-14563 implements the U3a row of TASK-14596's plan
(Founder THR336 msg85 approved TASK-14461 revision3 SOURCE; msg95 continue).
The routing union includes exactly `seq_source_waiting`, `seq_carrier`,
`seq_visit`. Unchanged selected scalar checks precede recognition. OrgState
constructs an inert `SequentialDispatcher` and mirrors it to DB/orchestrator;
classification requires that class and the same org slug. An absent or wrong-org
attachment remains `reconciliation_required`.

Every execution-facing consumer holds sequential ownership: admission refuses,
heartbeat does not write, routing/recovery do not enqueue or dispatch, ordinary
PID/native mutation paths refuse, and completion/cancel return the existing
reconciliation code. Non-legacy guards continue to fence jobs, portability,
result-action consumption and parent propagation. Pause discovery holds these
kinds and reports downstream execution evidence incomplete. All dispatcher ports
are refusing/no-op; it imports no Coding module and owns no writer or allocator.

This unit adds no sequential mark/task/run producer, source adoption, engine,
callback transition, host launch or return publication. U3b–U3e and U4–U9 remain
separate. Disposable SQLite structural observations and lint/static checks do
not prove V3 behavior, current authority, native settlement or ordinary-use
acceptance. No real-org install/migration, deploy, restart or activation follows.
See [source pointer](../../superpowers/specs/2026-11-08-sequential-engine-u3a.md).


## U3b inert admission and first activation (THR336 msg85/95)

TASK-14635 under TASK-14633 / ROOT TASK-14563 implements only U3b from
TASK-14615, as amended by TASK-14630 revision2 sections 2/3/5. This supersedes
that design's full-reader composition for admission. `sequential_engine.py`
exports `admit_uncommitted` and `activate_uncommitted`; no dispatcher, route,
startup, callback or other production caller attaches them. U3a's execution
holds remain. This U3b checkpoint did not provide U3c scopes. The inert U3c
source extension is described below; runtime capability/return/launch callers
remain unavailable.

The prospective native carrier and entry TaskRecords are validated before any
INSERT: canonical start bytes, immutable stored definition and selected settings
pins, latest generation, Allowed admission, complete independent role bindings,
current captured authority, the actual source task/principal/SID/INTEGER-result,
legacy v1 source ownership and pristine native tasks. The caller exclusively
owns a genuine `WorkflowAuthorityCoordinator.admission_writer(capture)`;
discovery happens before that context. All engine work inside is synchronous
SQL/pure validation, with no file/network/await/launch/notification/commit.

The writer defers FKs for its transaction, inserts the provisional run, native
carrier and v2 mark, adopts only the source's legacy v1 mark, writes the first
visit/native task/v2 mark, admitted event and `visit_queued` outbox, finalizes
pointers/counters, and validates before the caller commits. Outbox bytes are
stored only. The source/native IDs keep their existing meanings; run/visit/event
identities never become native task IDs or audit scope prefixes. No audit rows,
`active_chain` or `active_fanout` are produced. Failure must escape the writer
context and roll back the whole graph. First activation is not a separately
supported commit boundary. Exact start-key/bytes/source replay returns the
persisted IDs even when prospective native task IDs differ; changed bytes refuse.
Admission-only replay refuses a run that has already entered a later lifecycle.

`sequential_run_validation.validate_selected_run_uncommitted(conn, *, org_slug,
run_id, scope, evidence_ids=())` requires the caller's transaction and accepts
only `scope='admission'`. One fresh unchanged `_ReadBudget`, `_sql_budget` and
`_schema_snapshot` cover its entire acquisition. It returns the original budget's
diagnostic counters. It copies the temp-shadow refusal, compares selected
sequential/ownership structure to independent literal DDL/view references using
the parallel S1 `_candidate_structure` copy, and calls the unchanged selected
`_native_contract`. A named-table probe selects the document letter/coding flag;
column metadata comes from that matched structure.

Indexed selected row reads use a parallel `_rows` copy with explicit columns and
WHERE/args into unchanged `_bounded_rows`, plus the same per-row type loop.
Only this admission's run, first visit/event/outbox, native task/result links and
three ownership marks are validated. The selected definition is compiled once
per validation; selected settings/operation checks copy the reader's per-row
checks and use an indexed newer-generation probe. No settings generation-chain,
legacy history, full layout or full ownership inventory reader is called.
`read_sequential_owner` and copied v2 native checks validate each selected mark.
Requested evidence IDs use PK `IN (...)` reads into unchanged `_auxiliary`;
previous output `content_bytes` and visit `result_bytes` are never acquired.
Pointers, CAS/event/counter limits, the admission hash anchor and outbox closure
must all agree before success. Other scopes refuse before acquisition.

The full `read_sequential_history` remains the independent **offline oracle**,
including immutable generation history and older-row tampering outside this
selected write set. Decision 2 now supplies A/B/C/L under one original deadline
as described in the storage boundary above, with run-scoped ownership acquisition.
C still bounds cumulative run history and payload/copy/replay work; L bounds
org-wide guard and retained legacy SQL. These finite ceilings remain refusals,
not a universal capacity guarantee. U3b's selected validator remains admission-only.
The protected module's sentence “No normal startup, allocator, route or producer
imports this module” is now stale: this new validator imports its private helpers.
That docstring correction remains deferred; its original bytes remain intact.

Maximum-request disposable admissions, independent small-DB oracle reads,
negative rollbacks and lint/static checks are STRUCTURAL OBSERVATIONS only.
They establish no V3 behavioral PASS, actual callback/native settlement,
repository capability, live installation, activation or ordinary-use readiness.
Independent full review and scoped QA remain required. U4.0 remains NO-GO;
Decision 2 is delivered by TASK-14734. U3c, F4, SB-1 and held PR1252 remain separate.
See [U3b source contract](../../superpowers/specs/2026-11-08-sequential-engine-u3b.md).


## U3c inert lifecycle and selected validation (THR336 msg85/102)

TASK-14681 under ROOT TASK-14640 continues the preserved TASK-14677 candidate.
The engine adds synchronous session binding, result acceptance, transition,
return and cancellation functions. They use the caller's genuine
`WorkflowAuthorityCoordinator.admission_writer(capture)`; they do not commit,
launch, notify, await or perform file/network I/O. U3b admission/first activation
are unchanged. No daemon, route, startup or callback caller is attached.

The future trusted adapter supplies exact `content: bytes`, its declared
`contract`, envelope, genuine INTEGER task/result/session identity and verified
checks explicitly to `accept_result_uncommitted`. Content is not recovered from
a completion summary. The adapter must separately authenticate complete callback,
current authority, output access and positive native/outer settlement; fixture
result seeding proves none of these. Negative/return envelopes carry empty
content/recipients and create no output row.

`JSON_OUTPUT_LIMIT=49152` and `DOCUMENT_OUTPUT_LIMIT=1048576` implement Decision
1 = B. `check_output_size` is raw-length-only, before writes/decode/digest;
accept validation repeats the gate by an indexed raw byte-length read before
payload acquisition, then validates the one accepted output. Over-limit
refusal rolls back with `sequential_output_limit_exceeded`, no successful result
or advance. No result means a durable sequential hold cannot be represented by
the unchanged reader/reducer/schema: **F-1 remains open** for the owner before
U3d-1. A native terminal without accepted result may leave sequential
active/running meaning stalled; cancellation is the existing exit, not an
invented accepted result or durable hold reason.

Each writer ends in its own selected scope: session, accept, transition, return
or cancel; admission stays unchanged. Accept and transition have separate fresh
original budgets. Transition reconstructs prior counters/selections from
immutable transition records and output metadata, never prior output payloads.
It calls the unchanged reducer. Unknown bounded outcomes persist as literal
hold transitions; exhausted return/visit limits reserve zero successors.
Exact accept replay selects the original accepted visit. Transition replay
resolves the stored event for that operation and returns its original IDs even
after later progress/cancellation. Conflicting result/operation identity refuses
with no sequential changes. Return closes source/carrier and writes one
`run_return` outbox record; it sends no notification. Cancellation fences an
active or held run; native interruption and settlement remain later work.

**Evidence writer contract (EM ROOT14640 evidence-sizing ruling):**
`EVIDENCE_CONTENT_LIMIT=16384` bounds raw stored evidence bytes.
`check_evidence_size(raw)` raises the same typed `SequentialRefusal` with code
`sequential_evidence_limit_exceeded`, using length only before write, decode or
digest. U3c has no evidence writer. Every future U3d-1 evidence writer must call
this gate before insertion. Runtime-built evidence is distinct from agent/user
node output, so this engineering bound does not change Decision 1 = B.

The `evidence` selected scope accepts at most **two evidence IDs per call**.
The existing 8192-ID request guard is unchanged. Each selected row's raw content
length is checked by primary-key point read before content acquisition and the
unchanged `_auxiliary` digest/decode validation. A stored 16,385-byte row or a
three-ID call refuses with `sequential_evidence_limit_exceeded`. This local
writer/validator contract does not change the 1 MiB cell bound or full-reader
caps. The former 65,536-byte dense evidence probe cost 36,936 steps and exceeded
SB-5; it motivated this bound and is no longer a declared-limit case.

Sizing uses the two pinned fixtures, maximum escaped request, their declared
16-visit ceiling, 64 KiB result envelopes, JSON outputs of 49,152 bytes,
documents of 1,048,576 bytes, and evidence of 2 × 16,384 bytes. The binding
observation is late JSON accept with a maximum envelope: 30,820 aggregate
engine+validator steps; transition is 24,918,810 aggregate reserved bytes.
Evidence measured 12,362 / 20,569 validator steps for one/two dense rows at
admission and 21,909 for two rows after later progress. These remain below
SB-5's unchanged half-caps: 32 MiB / 4096 rows / 32768 steps / 2.5 seconds.
See the linked source spec for computed bounds and per-scope measurements.
These are charged-work/structural observations, **not behavioral PASS** or hard
RSS/time guarantees. The unchanged full oracle hits its existing inventory cap
on the 8193-unrelated-task and long Coding history probes; those refusals remain
explicit, with no reader acceptance or Decision2 resolution claimed.

No live org DB, installation, migration, execution, assignment, deployment,
restart, V3 or U3d/U3e/U4–U9 acceptance follows. General integration is SKIPPED
THR243seq42; Python units stay RETIRED and Product E2E is not triggered. Required
PR checks remain only Source Boundary Lint and Web Lint (Node 24), followed by
independent full review/scoped QA and manager guarded merge. Product PRD parity
belongs to product_lead / THR341 and is not edited or certified here.

[U3c source spec](../../superpowers/specs/2026-11-08-sequential-engine-u3c.md).


## U3d-1 native carrier and settlement consumer (historical source checkpoint; THR336 msg85/102)

TASK-14721 implements accepted TASK-14718 rev2 under ROOT TASK-14640. This
section supplies the caller missing from the historical U3c record above: U3c
still receives explicit content and never extracts a summary. Queue admission
remains unconditionally False. Launch/instructions/Pause ownership are U3d-2;
caller notification is U3e; U3f below now supplies document-only witnesses,
with repository domain/access witnesses held for U4/U5 remainder. No live
installation, restart, admission or V3 execution follows from source delivery.

For a genuinely bound visit, the existing completion file's summary is a STRING
containing canonical `workflow-sequential-callback@1`, exactly `format`, `outcome`,
`content_base64`, `findings` and `capability_claims`. Sorted compact UTF8 JSON,
strict padded standard base64, no duplicate/extra keys, floats or trailing newline.
Findings are at most32 own nonblank UTF8 strings; at most8 distinct declared-order
check/digest claims are unsigned references. No agent identity/recipient/OutputRef
or operation key is admitted inside the carrier. Metadata with empty base64 is
at most16384 bytes; summary at most1414488 bytes; other native metadata in
aggregate at most16384 bytes. Existing SQLite limits are read, never increased;
summary+metadata+4096 bytes must fit. Native summary readers retain their existing
visibility. Role recipients do not make this wrapper private. A later narrower
byte-custody requirement must STOP this use and return to the owner for a reviewed
custody design, without an auth/permission workaround.

Immediately after transport decoding, imported `check_output_size` checks ACTUAL
bytes before domain decoding, content hashing or native insertion: JSON49152,
document1048576 inclusive. Positive produced/APPROVE/PASS/merged requires nonempty
content; all other outcomes require empty content, own findings, no claims.
Failed/blocked native callbacks retain their cause and cannot report success.
The unchanged native wrapper is the durable callback-to-settlement carrier; its
positive INTEGER is obtained with same-connection last_insert_rowid and PK readback.
Callback writes no accepted sequential result/output. Exact full-projection replay
is read-only even after tracker removal, never advances and never chooses a new
result. Changed projection refuses; no automatic transport retry.

Safe terminal uses the original request/attempt/observed handle and Boolean
quiescence/clean-or-term-or-kill/no-survivors/no-cleanup-error predicate. Terminal
and outer evidence IDs are SHA256(canonical `{format:workflow-sequential-evidence-id@1,
run_id,visit_id,kind}`), selected only by PK. JSON binds the original native
quartet and carrier digest; SQL task/principal/session/result columns stay NULL.
Evidence is at most16384 bytes, gated before insertion/digest, and validated in
scope=evidence with at most two IDs. Initial native-result discovery is indexed
task/agent LIMIT2; ambiguity/foreign SID refuses, never latest adoption. Later
selection uses the original result PK. Unsafe/unknown/no-result does not invent a
sequential hold/result. F-1 option(i): native terminal plus sequential active/running
after session end means stalled; cancellation is the exit. Restart stays held.

Positive original outer COMMIT precedes a fresh authority capture/writer and
`_advance(run_id,visit_id,result_id)`. The daemon composes U1 findings/refs with
real identity and exact content digest, retaining distinct carrier/envelope/content
digests and separate content/envelope arguments to U3c. Current-subject capability
and recipient access require genuine authorized witnesses. At this historical
checkpoint every positive output refused `sequential_output_access_unavailable`;
the current U3f document-only exception is specified below. Repository/mixed
graphs retain that refusal;
unsigned claims never populate verified checks/receipts. Negative reports may carry
only the independently proven native-and-outer settlement check. Missing proof
means dependency/no advance, never behavioral success.

R1 identity H is SHA256(canonical `{format:workflow-sequential-advance-id@1,run_id,
visit_id,result_id}`) from the ORIGINAL source. Distinct event keys are
`seq-advance-transition-`+H (87 ASCII) and `seq-advance-return-`+H (83 ASCII).
Fresh pre-accept CAS=c; transition uses c; terminal return uses c+1 in the SAME
atomic writer (event sequences c+2/c+3; final CAS c+2). Replay selects authenticated
original transition.prior_cas=c and original source/arguments/keys, checks returned
sequence=(c+1)+2, and returns existing IDs without DML. Later CAS/active visit never
substitutes for the original; no successor allocation occurs on replay. Incomplete
or conflicting terminal pairs refuse and never fill a missing return. Cancellation
is checked before engine writers. A refusal rolls back only the new sequential
transaction; prior native/terminal/outer commits remain. Notification follows COMMIT;
queue remains False and caller notification remains absent.

The protected sequential_history.py:3 docstring predates selected validation's
private-helper imports and remains unchanged. Source/structural observations are
not behavioral PASS. Positive callback/host terminal/outer/advance, capability,
access and V3 remain UNVERIFIED. Python units are RETIRED; general integration is
SKIPPED THR243seq42; no Product E2E trigger. Independent full review, same-head
scoped QA and guarded manager merge remain mandatory. See
[U3d-1 source record](../../superpowers/specs/2026-11-08-sequential-engine-u3d1.md).


## Sequential queue and launch ports (U3d-2)

U3d-2 supplies source-only queue, native Pause preparation/claim/session binding,
host request/handle/PID and node-instruction ports. This supersedes U3a/U3d-1's
queue-always-False description for selected visits only. Admission still refuses
without a positive deterministic source `outer` evidence row read by primary key
using the U3d-1 evidence helpers (visit_id NULL). Nothing writes that row until
U3e. Settings remain Disabled by default; U3s's venue fence is not attached here.
Merged source is not production reachability or activation.

The run must be active, its selected visit queued with no SID/result, and its
native task pending with step count zero, no block or cancellation and no SID.
The dispatcher rechecks this before preparation and native claim. Current
authority serializes launch; the generic Database claim CAS is reused and U3c
`bind_session_uncommitted` alone writes running/SID. Queue notification follows
committed engine work; source/carrier remain held. Heartbeat remains a no-op,
without task stamps or audit rows. No sequential retry or native recovery exists.

`node_instruction` uses unchanged `render_node_brief` with the immutable request,
selected input refs, addressed stored inputs and attributed returned findings.
The fixed safety text remains separate from labelled untrusted node JSON; saved
refs/recipients do not grant new access or capabilities. Unavailable/oversized
brief inputs refuse under the existing renderer bounds. U3f below supplies
document-only addressed delivery and trusted witnesses; repository capability
stays held for U4/U5 remainder. The original bundled sequential callback
paragraph is unchanged, with one additive document paragraph. Actual launch, handle, PID, callback, terminal
and outer settlement through the host remain UNVERIFIED (V3).

### U3e-2 caller return consumer (source checkpoint)

U3e-2 attaches caller effects only to an independently validated committed
`returned` event and its immutable `run_return` outbox obligation. A completed
run without that return is ineligible. U3c closes source/carrier status and
completion times; the same writer projects the final native task's retained
summary/output directory, clears their block kind and records the run note.
The original source result/SID remain unchanged; the carrier gains neither.
After COMMIT the consumer rebuilds their existing task histories and requests
only the original source's ordinary parent wake. The shared helper independently
validates that source's return, then preserves parked/delegated, lineage,
all-children-terminal and generation checks. An explicitly supplied attachment
queue owns both `enqueue_if_absent` reservation and generation-aware publication;
an omitted queue retains the existing orchestrator queue behavior.

Returned-only rediscovery replays the same consumer for terminal sources. It
never resumes a sequential visit or mutates/acknowledges the outbox. The guarantee
is one immutable workflow return; queue insertion is not a durable caller
execution acknowledgement. Thread follow-up/SYSTEM/invocation minting are not
invoked. No fresh-start producer, public command or bundled skill change ships
in U3e-2. U3f supplies the document-only successful-output exception below;
repository U5 remainder, native recovery, venue/settings and Pause
backout holds remain. Source/static delivery supplies no V3 or restart proof.


## U3e-1a selected-run cancellation

The existing Founder task-cancel route stops one directly selected sequential
source, carrier or current visit. Under authority → teams → DB ownership it
rediscovers the complete selected run, rechecks its CAS and current authority,
then uses the unchanged U3c cancellation fence and stamps only live selected
native source/carrier/current-visit rows in that same transaction. Historical
visits/results and native SID/PID/journal ownership remain intact. Each changed
task receives the existing task_cancelled audit shape once. No result or source
session is synthesized. This attachment precedes the separately scoped U3e-1b
start/source producer; it grants no fresh-start or recovery capability.

The request key is `seq-cancel-` plus SHA256 of canonical `_bytes([run_id,
target_task_id, cascade, stripped_actor, stripped_rationale])`; an empty actor
defaults to `founder`. Fresh requests use the locked run CAS. Exact replay uses
that key's original cancelled event sequence minus two and makes no native,
audit or control effects. Changed target, actor, rationale or cascade after the
stop refuses. A source/carrier point stop (`cascade=false`) refuses when other
live owned work would remain; a current-visit point stop fences the whole run.

The ordinary ancestor guard follows indexed task/parent reads, including
terminal dependency paths, with one unchanged read allowance and at most 512
distinct task rows. Any sequential descendant, malformed/cyclic/incomplete or
exhausted acquisition refuses before Coding or ordinary mutation. A direct run
stop also refuses live nonmembers and mixed owners. This is a bounded refusal,
not U6 traversal. See the [U3e-1a spec](../../superpowers/specs/2026-11-09-sequential-engine-u3e1a.md).


## U3e-1b-i sequential start adapter

This source checkpoint attaches the closed `SequentialStartDecision` to the
existing completion seam: exactly `action=workflow_start` and the unchanged
`workflow-sequential-start@1` StartCommand. Only that exact format selects the
sequential adapter; Coding @1/@2 keep their existing validator and interpretation.
`CompletionBody.decision` advertises an `anyOf` with the unchanged Coding schema
first and the closed, fully inlined sequential schema second. The existing `if`
selector and `dict | None` transport stay unchanged; JSON Schema extras do not
perform runtime validation. The historical OpenAPI snapshot predates the Coding
schema extra and is retained without unrelated regeneration.

Fresh callbacks retain 404 → ownership classification → unsupported-owner 409 →
task-active ordering, then the existing manager/session/native-policy gates.
The exact sequential decision needs an actual executable team manager's `task`.
Before committing its callback, the route freshly checks the outside-live-home
venue marker and coherent current Allowed settings, definition/pins, bindings,
lower limits and source ancestry. Refusal is 409 `sequential_start_unavailable`
with zero callback/business writes. Malformed sequential decisions use 422
`sequential_start_invalid_decision`; wrong manager/source uses 409
`sequential_start_manager_result_required`. An adopted source accepts only its
original quartet and complete saved completion projection as read-only replay;
changed/fresh source callbacks refuse.

The consumer authenticates the actual INTEGER callback before another fresh
venue/read-only precheck and authority capture. Authority → teams → database
locks precede the sole admission writer. It revalidates the RetryClaim, actual
native invocation, current executable manager, noncancelled ordinary ancestry,
absence of chain/fanout/job wait and independent bindings. Thread-originated or
sequential-owned sources/ancestors refuse. D1 original limits are computed inside
the writer from the exact pinned settings snapshot; native allowances are never
replenished or converted. Native v2 spend/claim/ack uses only existing uncommitted
APIs and rolls back with the unchanged U3b admission/activation graph.

Run identity stays U3b's identity of org/source principal/operation key. Carrier
and first visit use `TASK-SEQ-` plus SHA256 of canonical `[run_id,"carrier"]` and
`[run_id,"visit",1]` respectively; collisions refuse. The single business COMMIT
parks the original source by changing only `block_kind=delegated`,
`note=sequential-owned:<run_id>` and aware `updated_at`; its status, principal,
SID, result, step and revision remain. Carrier/visit have no invented SID/result.
Initial exact admission replay returns original IDs; conflict or later lifecycle
admission replay holds.

This unit publishes no queue item and has no source-outer producer. The admitted
first visit remains ineligible: `queue_admission_allowed` stays false without the
positive original null-visit source-outer evidence. U3e-1b-ii owns that separate
producer and manager skill instructions. Existing U3e-1a cancellation, U3e-2
caller return, default Disabled admission, repository/mixed U5 successful-output hold,
Pause backout and stale-settings limits remain controlling. Marker removal
refuses the next fresh start; it grants no continuation/activation authority.
Structural fixtures and static lint are not genuine callback/host/launch,
restart, cancellation, return or two-template acceptance. V3/V3-0b/U5 remainder remain
UNVERIFIED; no live install/settings/admission/deploy/restart is authorized.

### Complete-reader recognition and native carrier preflight

The complete same-snapshot reader recognizes the entire closed sequential
callback in `task_results.decision_json`. In `workflow_coding_schema.py`,
`_validate_coding_data` passes its already validated sequential inventory and
original retained budget into `_refuse_coding_metadata`; its specialized type
masks follow canonical column names in the selected projection. In
`coding_start_history.py`, `read_queued` excludes recognized sequential sources
from Coding callbacks/runs/owners and retains only a separate local proof inventory.
Its existing native completion-audit acquisition additionally recognizes a strict,
duplicate-free full report matching exactly one such original result and its
actual task/agent, including any existing INTEGER-result/SID attribution. Native
self-evaluation and every non-decision member retain their residual marker checks.
Matched audits still pass full typed row equality; failed matches remain for the
original discriminator and its first refusal, with all charges retained on the
same budget. There is no general audit-location exemption or synthesized audit
field. Coding returned-only discovery composes `read_org_layout` on its original
connection/budget with the original legacy fallback, before selecting any returns;
notification ownership, routing and queues are unchanged. Successful recognition
continues through unchanged validators: independent corruption reports its own
first owning validator. There is no suppressed-refusal receipt or enclosing-reader
exception, and this additional readability supplies no admission or authority.

The command must match exactly one complete definition pin and historical
settings generation/digest/bindings in that snapshot; a referenced run must
match its actual INTEGER result and original command bytes. Recognition exempts
only outer `action=workflow_start` and the matched
`workflow.definition.identity=release/happyranch/coding`. Every other member,
including the native self-evaluation carrier, keeps the original reserved-value
scan. Other locations, forged context, malformed/duplicate envelopes and mixed
markers remain refused. Pending callback readability supplies no admission or
current authority. Genuine Coding @1/@2 keep their original parsers.

Before native carrier validation, the pure helper walks the unchanged native
models' declared closed field annotations. Each visited schema/value node
prepays 512 bytes, each model object another 4096 bytes, on the original budget.
Wrong-shaped list members refuse before native model construction; valid lists
are charged element by element without a new cardinality limit. The strict
parse reserves `20N + 4096K + 768(T+1) + 1024` per actual parse (raw length N,
opening containers K, lexical tokens T); original canonical comparisons reserve
`12N + 4096` and each examined definition 4096 bytes. Failed work remains spent;
there is no replacement allowance, refund or cross-call cache. These accounting
terms do not certify hard RSS or wall time.

Finite structural acceptance covers the pinned 16/3/2 run limits and 15000
ordinary tasks. Scope A uses the ROOT exception: at most 4608 rows, invariant
within each layout (I4198/J4492); its schema-row headroom is 3994/I and 3700/J,
and schema growth consumes that allowance. A non-row dimensions and every other
individual scope retain their 50% thresholds; native caps and units are
unchanged. Instrumented elapsed is report-only. Uninstrumented timing uses
warm-up plus five samples; relative comparisons apply only to outcome-matched
pairs, with matched ordinary controls for newly readable sequential subjects.
Authentic native-v2 spend/claim/ack plus graph rollback and genuine host/outer
settlement remain UNVERIFIED; labelled fixtures do not close those gaps.


## U3e-1b-ii source outer producer (ROOT TASK-14640)

The original ordinary/v2 source TaskInvocation now supplies the null-visit `outer@1` evidence through `sequential_start.source_outer_settled`. The unchanged StartCommand, admission pins and parked source quartet remain authoritative. Only native success with supported containment, Boolean quiescence, no survivors/cleanup error, safe outer cleanup, a clear tracker and positive original-owner close/release can qualify. Missing or uncertain observations never imply removal.

The existing evidence ID/body and PK conflict rules apply: exact re-observation adds nothing; divergent bytes refuse with rollback. The existing selected evidence validator runs in the writer, and the positive evidence commits before notification of the admitted first visit. Queue admission independently checks that row. Cancellation supplies no row or publication; source/carrier receive no synthetic result or session. Genuine host settlement, first launch, restart/races and U5 remainder remain UNVERIFIED(V3).

## U3f / U5a document witnesses and addressed delivery

U3f supplies source support for document-only recipient and capability witnesses
before M1. This supersedes earlier blanket U5 witness-dependency statements for
this subset only. Every node in the immutable definition must select
`immutable-document@1` or `document-review@1` with its registered completion check.
At U3f delivery repository contracts and mixed graphs stayed held; U4b3 below
attaches only Coding declaration composition/disclosure. Every nonempty
`capability_claims` still stays held; JSON, references and URLs confer no repository capability or effect.

Recipients are the sorted, distinct producer role plus declared current-consumer
roles, derived from immutable definition data. Original admitted fixed bindings
map those roles to actual principals. A fresh current authority capture is
validated under existing ownership, with active/executable/eligible principals
and declared independence. Role names and stored recipient arrays alone are
insufficient. Before rendering, selected refs must match the original stored
native provenance, exact content hash and recipient set and be addressed to the
actual invocation principal. No latest-output adoption occurs. The unchanged
renderer still refuses individual input values over 16 KiB, even though authored
document outputs may be accepted up to 1 MiB; acceptance does not guarantee a
later brief can disclose the entire value.

After original successful native terminal and positive outer COMMIT, composition
uses the already compiled definition, original start/input selection and actual
task/principal/SID/INTEGER. Produced documents require completed status and null
verdict. Document review APPROVE requires matching verdict, canonical JSON object
bytes and at least one selected immutable-document subject. The reviewer must
differ from every selected document producer and satisfy declared independence;
a contextual prior review may share its reviewer, including v2 second_read.
Only `immutable-document@1` and `independent-document-review@1` checks are issued.
They establish provenance and independent addressed judgment, not semantic truth.

The deterministic witness binds original run/definition/start, visit/node/epoch,
native quartet/carrier, input selection, content, sorted recipient-to-principal
map and terminal/outer digests. It uses the existing 16 KiB evidence-size gate
and existing envelope receipts/checks, without a new evidence row or signature.
Current authority is a fresh veto; time/generation never changes receipt content.
Original-source keys, c/c+1 atomic transition/return and read-only exact callback
replay remain unchanged. A post-outer refusal rolls back only new sequential
work; prior native/evidence commits remain. U4b1 records its finite expected
business refusals as the authenticated Held overlay described below; other
errors propagate. No partial-pair repair is allowed.

SB-5 uses each scope's own charged bytes/rows/steps/time under
33,554,432 / 4096 / 32768 / 2.5 seconds, without resets or cross-scope sums.
R-U3f-SB5-1 retains the required 32-node/16-role/late16 case. Protected `_entry`
has pre-existing uncharged definition compilation: **F-U3f-ENTRY remains open**
to charge or bound it on the reader budget. Paired B/candidate observations
must show identical `_entry` invocations and unchanged evidence/entry counters;
U3f adds no definition compile or compiled-tree clone. New history, evidence,
output and definition parsing/copies must be charged. That condition permits
publication, not a PASS of compile accounting.
Document binding context uses the charged start parser and reuses the resulting
bindings. U4b3 extends this charged context to code-change composition and
Coding input rendering. Other unsupported contracts retain empty advancement
context and B's lazy successor start parse.

R-U3f-SB5-2 requires fresh authority capture before ownership. In
`node_instruction`, the original reader budget starts before capture and charges
four snapshot JSON decodes at `48 * snapshot_bytes + 512` each, with token
traversal steps, plus both profile-mirror walks and digest-map work. The same
allowance/deadline continues through admission_writer, `_entry` and rendering on
document and non-document paths; document binding's additional decoder remains
separately charged. No duplicate canonical/effective-profile discovery is added.
**F-U3f-AUTH remains open, owner ROOT engineering_manager:** protected authority
configuration parsing (teams, agent definitions and effective profiles) is not
charged to any reader budget, a pre-existing class shared by dispatchers. This
classified follow-up does not block U3f publication and is not charged-accounting
PASS. F-U3f-ENTRY remains separate.

R-U3f-SB5-3 corrected C1 with document-only digest deferral; U4b3 extends
that charged branch only to code-change composition after binding validation. Before each serialization it reserves
`48 * 16384 + 512` bytes and one step on the ORIGINAL prefix budget; 16 KiB
is the unchanged `_evidence` input preflight. Unsupported non-code holds and
foreign-result refusals retain zero proof serializations; supported code-change
scopes now prepay the same proof copies. There is
no budget reset/refund, new parser or change to protected proof/key/CAS/effect
fragments. `_compose` keeps its original internal digest interface.
**F-U3f-MOVE historical checkpoint, owner ROOT engineering_manager:** PR-3a
removes non-thread MoveRequest/task-closure admission calls, including
sequential_engine and node_instruction. PR-3b removes the scoped thread/coordinator
readers; Founder settlement,
delivery-release permission, R1 retry and nightly-sweeper readers remain reserved.
The prior uncharged-parser disclosure is historical, never accounting PASS. The three disclosed
classes are F-U3f-AUTH, F-U3f-ENTRY and F-U3f-MOVE; any other NEW uncharged
history/evidence/definition/output work requires STOP to ROOT. Source
measurements do not certify hard RSS or OS timing.

Inline bytes retain the native task-result audience; recipients promise no
role-private custody. Stronger custody needs a separately approved design.
Source/SEED observations do not close M1 or prove native callbacks, settlement,
return/races or V3. Coding/U4/U5 remainder/native recovery remain held.
No live installation, settings, roles, admission, deploy or restart follows.
See the [U3f source record](../../superpowers/specs/2026-11-09-sequential-document-witness-u3f.md).

## U4a inert repository preflight (THR336 msg85/102/113)

R-M2-1 / ROOT TASK-15144 applies PRD v1.0.3 to U4. The Developer starts
resolve-only in its existing task, resolves actual repository/origin, base and
authorized scope, and verifies SHA1 before editing. The removed four-role
effective-duty-access checkpoint and readiness labels are not implemented.
Ordinary eligibility remains; configuration is not access proof. A prior check
is never a lasting disclosure/publication/action grant.

`sequential_repository_records.py` supplies closed immutable request/subject/
Held-cause/receipt records and a bounded canonical saved reader.
`SequentialRepositoryPreflight.resolve` is an **unmounted internal service**:
it checks the actual selected live code-change task/principal/SID and fresh
authority before observing that principal's configured clone, then rechecks
before returning metadata. It observes exact origin, SHA1 and full base commit,
validates supported relative scope syntax, and returns resolved/Held receipt
bytes. Raw origins, local paths and Git diagnostics stay private. Scope is the
actual task's declaration, not a permission grant; receipts are observations,
not effective-access or containment proof.

The observer refuses Git common-directory/gitdir indirection, gitfiles,
object alternates/http-alternates, grafts and loose/packed replacement refs
before Git and again at close. It reuses bounded no-follow metadata helpers
to reject linked or unsupported object topology. Closed config parsing refuses
include/includeIf; Git receives a fixed environment without inherited Git
overrides and retains `--no-replace-objects`. Unsupported topology produces
Held / `repository_unavailable`, with no subject. Shallow history is allowed
because it does not redirect identity/storage. These repeated observations
are not a racing-rename fence.

There is no production caller, public transport, evidence DML, persistent run
subject or lifecycle state change in U4a. A Held receipt does not mean the
database run has become Held. U4b2 now attaches the internal immutable subject
binding and resolve-only release described below. U4b3 now attaches repository/base/head/PR declaration composition and
downstream current-authority revalidation, as described below. U4b1 supplies the post-positive-outer
business-Held overlay below.
Another repository/origin/base or expanded scope needs a new authorized run;
correction heads retain the original subject. Existing preflight/observation
evidence slots are the planned storage seam; no schema change or live installer
is part of U4a. At U4a delivery Coding successful outputs stayed held. U4b3 below attaches
declaration composition; document behavior, Disabled admission and existing
start/settlement order remain unchanged.

U4a behavior/AC4 two-repository proof is NOT RUN. Python units are RETIRED;
general integration SKIPPED THR243seq42; required automated checks are lint/static
only. Full U4 split, caller inventory and pending evidence are in the
[U4 source specification](../../superpowers/specs/2026-11-11-sequential-coding-u4.md).


## U4b1 post-settlement business Held (THR336 / ROOT TASK-15144)

After the original positive outer COMMIT, only the finite typed advance causes
`sequential_output_access_unavailable` (including repository/mixed outputs),
`sequential_capability_unavailable`, `sequential_carrier_invalid`,
`sequential_carrier_limit_exceeded` and `sequential_output_limit_exceeded` become
business Held. The refused sequential transaction rolls back first. A separate
short native-owned writer reselects the original run/visit/task/principal/SID/
INTEGER, successful terminal, positive outer and exact callback, and validates
unchanged CAS/last-event/selection. This writer records refusal without granting
access, so it does not require a successful fresh admission capture. Current
cancellation or an authenticated competing transition wins. All other failures,
including corruption, ownership, SQLite and hold persistence, remain errors.

One transaction appends a deterministic closed `observation` in the existing
evidence table and sets visit/run Held with exact row-count/CAS checks. Cause,
node/role/principal, original source/proof digests, accountable-manager next
owner and no-automatic-retry/resume-unavailable limitation remain inspectable.
Accepted result stays NULL; no output, successor, new outbox, caller return or
retry follows. Event bytes/count/CAS/last-event remain unchanged. Complete and
selected readers authenticate the overlay against its active selecting prefix
and original proofs, including a later cancellation; unknown, conflicting or
mismatched observations refuse. Exact original outer redelivery reuses saved
IDs/cause without another hold or fresh access. A failed COMMIT claims no Held.

Document composition, 48 KiB JSON/1 MiB document limits and witnesses remain
identical. Coding tail still precedes sequential tail; native/Pause settlement
order and error aggregation are unchanged. No callback/agent-facing command or
instruction changes. Disposable retained-M1 snapshot diagnostics are scratch
evidence only; genuine native execution, concurrent races and V3 are NOT RUN.
Python units RETIRED; integration SKIPPED THR243seq42. No schema, admission,
settings, install, deployment or restart change follows.


## U4b2 immutable Coding subject (THR336 / ROOT TASK-15144)

The internal `SequentialRepositoryPreflight.bind` writer binds one successful
U4a receipt in the existing append-only run-level preflight slot. The original
issuer and authority/definition/start/request pins stay in canonical receipt
bytes. Both complete and selected readers authenticate digest, original first
code-change visit, native parent/agent/SID and run pins. All four native
provenance columns and the run-level visit_id stay NULL; no result is fabricated
or later backfilled. Schema and historical evidence remain unchanged.

The selected Developer receives resolve-only instructions until positive subject
COMMIT. Only a successfully returned bind releases editing under that original
subject. The writer repeats exact current SHA1 repository observation outside
transaction ownership and revalidates fresh authority/session/configuration and
pins before committing. No Git/filesystem/await occurs inside its transaction.
These metadata bookends supply no racing-rename fence or permission grant.

Exact replay preserves original bytes/IDs; narrower requested prefixes only reuse
the original scope. Corrections inherit the same repository/origin/base/scope.
Another target/origin/base or scope expansion commits durable Held with a finite
cause, accountable manager and new-authorized-run/no-automatic-retry action.
Unsupported preflight can hold before a subject exists. Its distinct closed
repository-hold observation shares the existing Held reader seam, with NULL
native provenance, unchanged event/CAS prefix and no output/successor. Both
readers authenticate it; cancellation is the only later lifecycle event. Same-session
exact Held replay acknowledges original bytes without probing or releasing edits.

The authenticated original live task/principal/SID may still commit its canonical
negative `BLOCK` callback under current authority, with empty content/claims and causal
findings. Success-shaped callbacks and foreign sessions refuse; exact full-payload
replay retains the one original INTEGER result. Original safe terminal/positive outer
settlement drains without sequential acceptance: run/visit stay Held, accepted result
stays NULL, and no output, successor, editing release, resume or retry follows. Native
completed/failed status is retained; a blocked callback keeps its blocked
result/classification and closes the native task as failed after safe terminal proof.
Cancellation wins over fresh callbacks/settlement; an already committed callback may
only replay unchanged. The immutable subject, hold bytes/cause and event prefix are
preserved.

This is an internal source attachment, without a public route/CLI/TS adapter or
new admission/settings/permission/credential/grant. U7 owns transport; report an
unavailable adapter. No four-role effective-duty checkpoint is restored. U4b3
now attaches code-change declaration output and downstream current-authority
revalidation below; U5 owns review/QA/merge. Disposable scratch observations use explicit synthetic
pre-result native/authority/live scaffolding, not genuine native/V3 acceptance.
Full correction progression, racing filesystem changes and native first-edit
ordering are NOT RUN. Units RETIRED; general integration SKIPPED24342; all CI is
lint/static only. See the current [U4 spec](../../superpowers/specs/2026-11-11-sequential-coding-u4.md).


## U4b3 Coding composition and ACCESS-1 (THR-336 msg131)

TASK-15167 / ROOT TASK-15165 implements R-M3-1. Founder ACCESS-1 msg131 is
controlling over the earlier PRD v1.0.3/U4 access-observer language; PRD v1.0.4
publication is tracked in THR-351. Agents share one OS user and one gh credential,
so the runtime cannot independently observe per-agent effective repository access.
The daemon rechecks current eligible/active fixed principals and unchanged saved
subject at publication/disclosure; it performs no GitHub/network I/O or fetch.
The merged local clone metadata preflight is unchanged. Downstream reviewers/QA
prove access through their own exact-head fetch; final-manager expected-head
non-admin guarded merge remains U5. Failed own operation -> BLOCK -> Held,
with denied versus unverified findings. No four-role access checkpoint returns.

At the U4b3 checkpoint only code-change@1 produced/completed/null-verdict callbacks could compose an
ordinary result envelope under genuine task/principal/SID/INTEGER, original
successful terminal/outer proof and current authority. Canonical JSON (49152-byte
maximum) has exactly `format=workflow-sequential-code-change@1`, complete original
`subject`, current visit `epoch`, full lowercase 40-hex SHA1 `head`, and
`pr={repository,number,url}` with owner/repo, positive INTEGER and matching
https://github.com/owner/repo/pull/number. Full original subject equality includes
repository ID/key, origin digest, SHA1 base and scope. These are declarations,
not daemon proof of PR/head existence, remote association or diff scope.
Capability claims stay empty. Deterministic checks bind the original native and
subject/content/recipient/proof context; U5b1 below extends assessment provenance.

Every Coding selected-input rendering rechecks current authority before returning
bytes, plus exact selected ref/provenance/hash/original recipient set and saved
subject for code-change inputs. Authority positively lost is
`sequential_authority_denied`; unavailable/stale authority is
`sequential_authority_unverified`. Disclosure blocks preserve bindings, recipient
set and selection with zero returned bytes. Successful composition failures use
U4b1's independent rollback-then-Held writer, adding finite code-change-invalid,
subject-changed/unverified and authority-denied/unverified causes. Failed COMMIT
never claims durable Held or publishes output/successor. Native settlement and
document witness behavior remain unchanged. No DDL, transport or new grant.

Source-only delivery includes disposable synthetic diagnostics, not real native/
provider/full correction/V3 acceptance. U5 and U7 remain separate. See the
[current U4 specification](../../superpowers/specs/2026-11-11-sequential-coding-u4.md#access-1--u4b3-attached-source-task-15167-root-task-15165)
for the declaration, cause and evidence boundaries.


## U5a Coding evidence reader and policy (THR-336 / TASK-15200)

PRD v1.0.4 SHA256 `96925395de4e44cc5c1570deb512ec843fcfd72c0b3eaeff0423c00b58a07bb5`
and Founder msg131 ACCESS-1 control current-authority versus agent-owned access.
The new unmounted `sequential_coding_evidence.read_current_coding_evidence_uncommitted`
performs SELECT-only full selected-run reducer replay, immutable admitted pins,
original INTEGER callback/carrier, successful host and positive original outer
proof joins under the caller's owned current-authority transaction. Original
subject and current repository/base/head/PR/epoch must agree. Current active
fixed worker/registered-manager bindings are rechecked before and after deriving
policy; unavailable authority is unverified, proven lost eligibility is denied.
No Git/network/access observation occurs.

`sequential_coding_policy` unconditionally requires independent-code-review@1
APPROVE and code-qa@1 PASS, regardless of Final inputs or omitted/rerouted graph
nodes. Assessment content is exactly canonical
`{format:workflow-sequential-code-assessment@1,change:<whole code-change declaration>}`
with the existing 49152-byte JSON cap. That subject assertion is not approval;
only the original settled native verdict/carrier can qualify. All retained
code-change visit principals remain contributors across invalidated epochs;
reviewer/QA/Final are distinct and absent from that complete set. Existing
intent/action evidence refuses without another effect. Missing review/QA,
staleness, non-independence, denied/unverified authority and uncertainty raise
finite causes with no returned admissibility/executable selection/intent/action
or SQL mutation. U5a does not persist Held.

The successful in-process derivation carries original evidence/prefix pins and
requires exactly Source Boundary Lint and Web Lint (Node 24); it is not a lint
observation, live invocation proof, executable selection or lasting grant.
U5a delivered no runtime caller/route/CLI/producer. U5b1 below attaches assessment
producers; Final successes remain held. U5b2 must prove the genuine live Final,
re-read at each boundary and supply the common LOCAL Final-manager core/checks/
one owned guarded action/independent remote observation. Historical Coding
adapters, U4 owners, default admission and document behavior remain unchanged.
Synthetic out-of-repo scaffolding is not native/V3/remote acceptance. Full split,
caller inventory, effect/settlement separation and NOT-RUN obligations are in
[the U5 specification](../../superpowers/specs/2026-11-11-sequential-coding-u5.md).


## U5b1 Coding assessment producers (THR-336 / TASK-15222)

The dispatcher now composes independent-code-review@1 APPROVE and code-qa@1 PASS
with completed status, matching original native verdict and empty capability
claims. Content uses U5a's closed assessment syntax and unchanged complete selected
code-change declaration. One selected change is required; all selected Coding
upstream content/verdict/ref and original successful native host/positive outer
proofs are re-read. Current active fixed bindings and immutable subject/epoch
must match. Reviewer/QA cannot assess their own selected upstream result or any
retained code-change contributor, including invalidated epochs. Their own live
exact-head fetch establishes effective access; the daemon observes no remote data.

Addressed assessment disclosure rechecks current authority, original recipients,
exact selected ref/hash, current subject/epoch and settled provenance. Denied and
unverified authority refuse with zero returned bytes and unchanged selection.
REQUEST_CHANGES/REVISE/BLOCK retain empty output/claims and own findings with
matching verdict; they issue no domain checks. Existing return/hold/counter
semantics remain. Refused successful publication rolls back before the sole U4b1
finite durable Held writer, with no accepted output/check/successor for that visit;
a failed COMMIT never claims Held. The same original finite budget pays decoded
traversal and native reads; exhaustion stays a reader refusal. No new cause or
schema is needed. U5b2 Final capability/local execution remains unavailable,
including guarded-repository-merge@1 success. No activation, access observer,
credential, daemon network I/O or historical Final adapter change follows.


## U5b2a internal Final interface (THR-336 / TASK-15240)

The unmounted `sequential_coding_final` service joins the actual original running
TaskInvocation/Pause/host handle/session with U5a full current-evidence derivation
in one current-authority boundary, then rechecks native pins and authority before
returning selection metadata. Omitted review/QA still reaches mandatory policy.
Original configured origin, subject, native workspace and all selection/result
pins are fixed. No executable command or lasting grant follows saved metadata.

Closed internal local records/readers and `workflow_sequential_final_journal`
supply a reader-first U5b2a split. A fresh genuine selection adapter is required
before exclusive original-Final-task reservation and intent publication; cwd,
operation key or path variants cannot replace that fence. Partial/error facts
remain reserved. Intent syntax requires the two exact successful selected-H lints;
syntax alone proves no provider observation. Original command result and later
remote MERGED observation remain separate; failed/lost commands stay uncertain.

U5b2b still owes common local CLI action/check observation, expected-head non-admin
guarded merge, genuine callback validation and mount. U6 mixed-history closure
and U7 task-session transport also remain. Final success keeps its existing
unavailable-output Held; the daemon performs no GitHub/network I/O. No schema,
historical Coding ABI, authority/permission owner or native settlement changes.
ACCESS-1/shared OS-user and gh credential limits remain. See the
[U5 split and caller inventory](../../superpowers/specs/2026-11-11-sequential-coding-u5.md).


### U5b2b1 unmounted local action adapter (TASK-15264)

The historical Final CLI now consumes `workflow_final_local` for its unchanged
raw lint readers and one guarded command/receipt/separate-read sequence. Its
historical commands, selections and output families retain their interpretation.
The unregistered `workflow_sequential_finalize` adapter consumes the concrete
U5b2a live selection service and one original-task journal fence, raw selected-H
check/run/status snapshots and compatible local record readers. It persists
intent before action and reselects current authority/native ownership plus local
immutable repository identity immediately before the expected-head non-admin
guarded merge. Failed/lost commands remain uncertain after a separate MERGED
read; exact remote SHA1/aware time is separate from native settlement. No caller
Boolean CI pass, new credential, access observer or daemon PR I/O is supplied.

This is U5b2b1 source only. U5b2b2 still owes genuine callback-observation
validation through both history readers and existing business-Held/cancellation
owners before mounting any producer/command. U6 mixed history and U7 task-session
transport remain. Final success stays unavailable-output Held; local diagnostic
`merged` grants no output/witness/advance. Existing Pause/Resume original admission
generation, cancellation, schema and ACCESS-1/shared-user/gh limits are unchanged.


## U6a inert mixed-history interface (THR-336 / TASK-15287)

An explicit `read_sequential_history(..., task_id=...)` returns an immutable
mixed native task/result-identity/owner closure after the unchanged complete
sequential/document/old-Coding oracle validates. Default callers keep the
original result/path. The unmounted selected wrapper uses that same complete
oracle and caller snapshot. Parent/child and workflow-member fixed-point reads
include terminal paths, retain exact native values and positive INTEGER callback
quartets, reject missing/cyclic/foreign/dual ownership and charge original D2 C
work/deadline after retained L. At most512 distinct tasks; exhaustion refuses
without partial output or new allowance. Result identity lookup does not parse
callback text or prove successful host/outer settlement or effective access.

U6a adds no producer. U6b still owes mixed Founder cancellation/cascade and native
root-tail composition; existing mixed refusal/direct selected cancellation stay.
U5b2b2 callback validation/mount and U7 transport remain required; Final success
stays Held. No graph/task/result/effect/budget/Pause history is rewritten. See
[U6 specification and remainder](../../superpowers/specs/2026-11-11-sequential-coding-u6.md).


## U6b1 unmounted mixed stop producer (TASK-15301)

`sequential_mixed_cancel.cancel_mixed` consumes the U6a full mixed oracle before
and after one admission writer transaction. It separates the normal live
ordinary cascade from terminal dependency discovery and whole sequential-family
fences. Live document/old-Coding mutation refuses pending their original writer
composition. Retained-family or unrelated native row drift rolls back the whole
transaction. Native rows outside the precise live write set, original result
identities, returned/cancelled graphs and all limits stay unchanged. Existing
engine fences own CAS/event ordering; original task_cancelled audits retain their
shape. Only an already committed callback can drain unchanged through the
existing dispatcher, without new result/visit/output/Final work.

This interface is **unmounted**. It returns opaque controls and captures original
root transitions through COMMIT, but performs no Stop, native-tail consumption,
cleanup or publication. Controls may run only after COMMIT/outside locks. Any
prepared/running/unknown journal/control ownership retains teardown-incomplete;
retained cancelled sequential graphs conservatively retain it too, pending the
U6b2 positive-original-owner composition. No PID or successful control return
supplies settlement. U6b2 must compose live retained-family writers, authenticate
cross-family ordinary overlays, refine native-tail disposition and attach the
Founder cancellation/cascade route and native-tail consumer. Existing mounted
paths, U6a/U4/U5, Pause and Final receipt uncertainty remain unchanged. No schema,
endpoint, retry or execution entitlement is added. U5b2b2/U7 remain required;
Final success stays Held. See the [U6 remainder](../../superpowers/specs/2026-11-11-sequential-coding-u6.md).


## U6b2a unmounted original-owner disposition (TASK-15325)

`sequential_cancellation_tail.cancelled_owner_disposition_uncommitted` reads
complete mixed history in its caller's original lock/snapshot/allowances, then
qualifies one original cancelled source/visit against actual native host, close
and release observations. Missing/prepared/unknown/uncertain or still-owned
observations remain unsettled; conflicting history/identity refuses. It writes
nothing and clears no owner. A positive one-owner observation is neither a
reusable receipt nor whole-root clearance; every other captured owner and native
root protection remains controlling. No caller is mounted. U6b2b still owns live
retained-family/cross-family overlay composition, owner aggregation/history and
Founder/native-tail attachment. U6b1, old Coding/document and U4/U5/Pause/Final
owners stay unchanged. See the [current U6 remainder](../../superpowers/specs/2026-11-11-sequential-coding-u6.md).


## U6b2b mounted mixed cancellation (TASK-15349)

This supersedes the earlier U6a/U6b1/U6b2a unmounted/remainder statuses. The
existing Founder cancel/cascade selects one mixed admission transaction for
fresh sequential stops and sequential-dependent ancestry. It reuses Coding's
original inner command/event/native-overlay writer and document draft/review
inner cancellation events. Detached native rows use the mixed overlay owner;
each live task has one overlay/audit. Original complete readers, immutable
returned/cancelled graphs, terminal dependency traversal, point/hidden-live
refusals, post-oracle rollback and COMMIT-before-Stop remain controlling.
Standalone ordinary/document-only paths keep their behavior. Historical live
document roots without an existing dispatch owner still refuse.

Manager Option B composes proof owners. U6b2a remains byte-identical and selects
only original sequential source/visit invocations. Other captured owners use
only their existing original family qualification. Old Coding reads its own
committed outer/native settlement; ordinary/document invocations have no
existing positive cancellation-outer qualification, so the specified fallback
keeps them UNSETTLED. Journal-only originals, prepared/unknown/missing controls,
unsettled outers and lost originals remain incomplete. No invocation is
fabricated and no native ownership is cleared to force settlement.

The ephemeral `CancelOwners` handoff retains the original root captures through
COMMIT and original native close/release/family callbacks. One positive of many
cannot deliver safe disposition. Only all captured owners positive permits the
existing safe root-tail consumer, still subject to native protections. Negative
or impossible qualification delivers one incomplete batch; no cleanup retry or
durable receipt is introduced. U5b2b2/U7–U9 and native/V3 acceptance remain
separate. See the [current U6 owner table](../../superpowers/specs/2026-11-11-sequential-coding-u6.md#u6b2b-mounted-composition-task-15349).


The U6 mixed stop authenticates combined history before composing legacy document lookup
and the original cancelled document/Coding terminal/outer consumers on that same
connection. Legacy consumers preserve sequential rows; the complete changed target is
authenticated after scope reset. Default standalone validation remains unchanged. This
composition repairs mounted cancellation without changing captured ordinary/document
UNSETTLED qualification or granting native/V3 acceptance.

### U5b2b2 Final observation declaration / unmounted reader (R-M3-3)

`workflow-sequential-coding-final-observation@1` is closed canonical JSON, total
<=49152 bytes, exactly format/change/selection_digest/intent/command/remote.
It retains the full selected change and saved intent including both complete
lint-observation records, a succeeded=true/exit0 command, and separate remote
MERGED/full actual commit/aware-time observation, with all original digest links.
Only `merged` uses these bytes in the unchanged generic callback carrier.
Failed/uncertain/missing command or recording failure (`command=None`, TASK-15275)
uses causal BLOCK, empty content/claims, unchanged local records and no reissue.
Provider page dumps remain local; syntax is neither an access observer nor proof
of per-agent credential isolation.

The unmounted `read_final_callback_uncommitted` composes complete/selected U6
mixed history, re-reads original INTEGER callback/host/positive outer proofs,
current ACCESS-1 authority/unchanged subject, selected pins and three mandatory
settled independent upstream results. It refuses malformed/stale/foreign/
replayed/changed, denied versus unverified, cancellation/Pause/Held and exhausted
reads with zero candidate writes. R-M3-2 retains per-complete-read D2 allowances
on one original deadline. No production caller, hold/acceptance writer or local
action mount is added. Existing successful Final rollback-then-Held and callback
drain/cancellation/Pause/receipt uncertainty remain unchanged.

R-M3-3 assigns **U7** selected task-session-only Final transport, local adapter
mount in the genuine live Final-manager session, validation acceptance wiring,
and the decision to admit guarded-repository-merge@1 success. U5b2b2 does not
mount execute_selected_final or wire _advance. Final success stays Held; there
is no daemon GitHub/network I/O, second CLI OrgState, historical family reuse,
new endpoint/public command, credential/permission/access observer or deployment.
See [U5](../../superpowers/specs/2026-11-11-sequential-coding-u5.md).


## U7a common read projections (TASK-15465, R-M3-4)

Six existing-Founder-bearer GETs expose stored sequential definition versions,
repo-free settings, runs/detail and source/carrier/visit task links, under
`/workflows/sequential` and `/tasks/{task_id}/sequential-workflow`. Exact paths,
closed records, audience/privacy and remaining U7b–U7d work are in the current
[U7 specification](../../superpowers/specs/2026-11-11-sequential-coding-u7.md).
SELECT-only snapshots authenticate complete history and fresh current authority;
GET allocates/writes nothing. Absent-task404 precedes ownership409. Denied/foreign
reads return no workflow content or link. Existing Founder task-result visibility
supplies authored-content access; agent sessions retain their addressed readers.
No managed-connector allowlist or credential/permission changes. TS mirrors,
classification and OpenAPI ship together; retired Vitest is not restored.
U7b settings CAS and original lookup are implemented in source below. The
U7c/U7d sections below supersede this earlier transport/Final-Held checkpoint.
U9 mounts public Coding selection and old-fresh refusal atomically; no human/
browser start. ACCESS-1/shared-OS-user/gh limitations, original Pause/receipt
uncertainty/cancellation and historical families remain.


## U7b repo-free settings and original save (TASK-15482)

The existing Founder bearer now supports PUT `/workflows/sequential/settings`
with closed canonical settings-put@1 (definition pin, bindings, limits, expected
generation and stable key), and separate GET `/workflows/sequential/settings/operations`
with operation_key query. Save appends only compatible immutable settings/operation
rows, preserving current admission (initial Disabled) and allocating zero work.
Same-key/body replay and lookup return the original receipt/snapshot, separately
from U7a's latest settings GET; a changed body refuses. No schema or historical
Coding settings/key-reader change. Exact routes, closed shapes, limits, native
writer rechecks, client custody and evidence limits are in the current U7 spec.
The browser provider retains a frozen org-local pending/uncertain operation with
one PUT, matching-original-only reconciliation and no locale-triggered work.
No new rendered UI, bearer fallback, agent/native audience entitlement, remote
allowlist, repository setup selector, admission action, fresh start or Final mount.
Final success stays Held; U7c/U7d, U8/U9 and native/V3 acceptance remain separate.


## U7c1 selected task-session reads and LOCAL Final mount (TASK-15494)

Two task-session-only GETs expose the committed subject at
`/workflows/sequential/repository-preflight` and the live selected Final manager
selection at `/workflows/sequential/final-manager`. Subject read is zero-write;
unbound subject refuses and does not bind/release editing. Both derive native
task/principal/current SID from the original binding, reject browser/bearer,
foreign/recovery/ended sessions, and bookend current ACCESS-1 authority, full
mixed history, native owner and cancellation under a SELECT-only snapshot.
Native SurfaceAllowList excludes the two new GETs. Existing audience rules,
managed allowlist and auth grants are unchanged; no TS/browser mirror.

`workflows sequential finalize` mounts U5b2b1 locally through fresh HTTP
selections, without a second CLI OrgState or daemon effect/network I/O. Original
intent/fence/expected-head non-admin action/separate-read/receipt uncertainty
remain. The command saves/prints the compatible unchanged callback carrier;
only the genuine manager's own FINAL callback supplies native provenance.
U5b2b2 validates after positive outer settlement; **Final success remains Held**.
U7c2 still owes compatible Final witness/output acceptance, U7d agent initiation,
and U9 atomic fresh-start cutover. Full command/route grammar, audience exclusions,
per-read allowances and SYNTHETIC evidence limits: [current U7 spec](../../superpowers/specs/2026-11-11-sequential-coding-u7.md).
No schema/permission/credential/access observer or live operation is introduced.

## U7c2a unmounted Final witness (TASK-15549)

The compatible Final consumer now rereads the actual selected callback through
the original complete reader and derives deterministic receipts/verified checks
and addressed recipients in the caller's original locked transaction. It binds
the complete canonical observation digest, original INTEGER result and
carrier/host/outer proof digests, immutable selection pins and fixed recipients.
All new copies/walks prepay the SAME original allowance/deadline; refusal escapes
unchanged and the original SQL hook is restored. There is no production caller,
write, effect or admission. **Final success stays Held.** U7c2b still must supply
complete output-history acceptance and same-transaction publication/return
validation before mounting (subsequently supplied by U7c2b); U9 now mounts
public fresh Coding selection together with the old-fresh fence.
The [current U7 spec](../../superpowers/specs/2026-11-11-sequential-coding-u7.md)
records the pre-edit split, witness preimage, allowance accounting and evidence
limits. ACCESS-1/current authority, agent-owned effective access, original Pause,
receipt uncertainty/no reissue, U6 and historical owners remain unchanged.

## U7c2b current Final admission (TASK-15564)

This supersedes earlier dated U7c1/U7c2a Final-Held checkpoints. The post-outer
dispatcher now admits validated Final success in its ORIGINAL locked authority
transaction. Complete replay independently authenticates the persisted entire
observation, INTEGER callback, original terminal/outer, selecting event/CAS,
immutable subject, retained current-slot upstream development/APPROVE/PASS and
exact recipient/check receipts. Same-transaction complete mixed target validation
checks acceptance, transition/successor, terminal return and original caller
fields before COMMIT/notification. Duplicate callback validates persisted
publication without DML or duplicate notification. Late failure rolls back all
staged publication. No LOCAL action is repeated.

Original caps/deadline and R-M3-2 per-read allowances remain; new parse/copy/walk
work prepays the original allowance and SQL hooks return to their owner. Genuine
pre-write exhaustion escapes without DML/COMMIT; late target refusal rolls back
staged DML. Malformed history stays unverified; current authority denied versus
unverified remains distinct. ACCESS-1 rechecks current fixed eligible principals
and unchanged subject/origin only. No daemon GitHub/network observer, credential
or effective-access claim. Original Pause, receipt uncertainty command=None/no
reissue, cancellation, U6b2a and Option B UNSETTLED retain their owners.

U9 now mounts public fresh Coding selection and old-fresh refusal atomically.
Native/V3/V5/M4 and genuine
access/merge/activation acceptance remain separate. Source diagnostics are
out-of-repo SYNTHETIC; units/Vitest RETIRED, integration SKIPPED24342 and no E2E.
See the [current U7 spec](../../superpowers/specs/2026-11-11-sequential-coding-u7.md#u7c2b-complete-final-admission-task-15564).

R-M3-6 / TASK-15570 authorizes the single additional protected-owner exception
`sequential_views.py` for browser-only Final redaction. After unchanged complete
replay, an accepted `guarded-repository-merge@1` output's existing `content_utf8`
contains only canonical `{"format":"workflow-sequential-coding-final-summary@1","outcome":"merged"}`.
Successful Final visits also project `findings` as an empty array: arbitrary
finding text can carry native material, and the existing outcome conveys success.
No observation or finding text, diagnostic, native pin/path/SID/handle, intent,
command, remote record or other native proof is copied. `content_digest` still identifies
the ORIGINAL stored output, not this browser summary. OutputView/RunDetail and
all route/OpenAPI/classification/TS shapes stay unchanged; every other output
contract retains its original verbatim content. Stored observation/carrier bytes,
complete/selected history, witness preimage and all readers/validators keep their
original behavior. This is solely a browser disclosure boundary, with no new
agent capability, access grant or LOCAL action. Synthetic B/H projection parity
and prior-H RED / exact-head GREEN evidence are in
`dev_agent/output/TASK-15570/handoff.md`. TASK-15571 found a sibling findings leak;
TASK-15572 extends the existing keeper with nonempty native-bearing findings and
records its correction in `dev_agent/output/TASK-15572/handoff.md`. Prior failure
history remains; no native/V3/real-merge claim follows.


## U7d ordinary sequential-start completion (TASK-15578)

The unchanged sequential StartCommand is consumed only through its executable
team manager's own ordinary `workflow_start` completion. Sequential intake now
requires the CLI label with no browser Origin, and under the original binding
lease compares supplied identities to the actual tracker org/task/principal/SID,
rejects recovery and requires completed status without a job wait. The label is
not a credential. The existing venue/settings/pin/ancestry precheck precedes
result insertion. The existing result consumer derives the positive INTEGER
from the committed native PK/full report and authenticates the original native
invocation before atomic native-action/graph/source parking. Pending replay after
tracker clear also selects the saved sequential decision and compares the full native payload, including when the incoming action changes.
Exact full replay keeps original bytes/IDs; changed body/key reuse refuses and
transport loss never authorizes reissue. Cancellation, original limits and positive original host/outer
settlement before first-visit release remain with their unchanged owners.

Public live fresh start remains OFF: both intake and consumer still require the
unchanged disposable-venue guard, which refuses the live daemon home/aliases and
absent/invalid/nonmatching runtime/org/DB-inode marker. U9 owns atomic new public
attachment/release selection plus old-fresh-start fencing; no flag shortcut or
historical engine fallback is added. Historical start@1/@2, original return and
source/thread association, U7a/U7b/U7c, Pause/U6 and LOCAL Final/receipt uncertainty
are unchanged; thread sources still refuse, notifications remain excluded.
No new route/schema/credential/permission/access observer or daemon network I/O.
See the [current U7 source and evidence limits](../../superpowers/specs/2026-11-11-sequential-coding-u7.md#u7d-ordinary-source-completion-consumer-task-15578).

## U9 release boundary and atomic cutover (TASK-15624)

`sequential_release` reads only the fixed reviewed bundled Coding v2 resource,
checks source/canonical bytes and the full compiler/validator/interpreter/
capability pin, then uses the common engine. There is no v1/filesystem fallback.
U9b mounts this fresh-only selector together with `coding_fresh_start_retired`
at old callback intake and consumption/inner writers, after authenticated exact
admitted replay. Literal old @1/@2 syntax/history, callback/settlement/cancellation
and LOCAL Final remain with their original family owners.

Public Coding fresh start independently requires mounted native runtime and
compatible storage, current authority/eligible roles, and latest Allowed NEW
settings. Any Coding identity/version goes through reviewed-v2 selection; a V3
marker cannot divert it into the fixture lane. Noncatalog V3 fixtures keep the
original disposable fence and common engine. A missing marker grants no authority.
No endpoint, flag, startup installation, old-settings conversion or legacy
progression fallback is added. Only the genuine original ordinary manager source
callback initiates work; positive original host/outer settlement still releases
the first visit.

Original-pin validation authenticates the immutable definition/settings/operation
snapshot and all native/history checks without a latest-generation veto. Fresh
`sequential_start._selection` AND engine `_prepare_admission` retain that veto
and their original codes. A valid admitted run previously stranded solely by
newer Allowed/Disabled defaults can continue at its original pins; no saved row
changes, re-pinning or budget replenishment occurs. Invalid/missing/originally
Disabled pins still refuse. Existing limits and R-M3-2 per-read allowances remain.

Offline setup/admission below remains separate authority. Source delivery and
out-of-repo SYNTHETIC diagnostics do not prove live installation, loaded identity,
native/V3/V5/M4 or operator acceptance. Keep the known installer8192-row/64MiB/5s
and8192-owner production-history limitation for follow-on work. See the
[compatibility table/full caller inventory](../../superpowers/specs/2026-11-12-sequential-coding-u9.md)
and [operator install/backout](../../runbooks/sequential-coding-cutover.md).

## U9b inert operator registration and admission (TASK-15616)

`scripts/setup_sequential_coding.py` is an explicit cooperative offline operator
entry point. `register` uses only the reviewed bundled Coding v2/full ABI pin and
appends a missing definition without defaults or admission; an exact existing
definition is a read-only no-op. Existing Workflows Setup Save creates new repo-free
defaults initially Disabled and preserves admission on later saves, unchanged.

A separate `admission` operation takes canonical <=16384-byte
`workflow-sequential-coding-admission@1` with exactly format, operation_key,
definition, expected_generation and admission (Allowed/Disabled). It requires
already saved NEW defaults, copies their bindings/limits and advances generation
under CAS. `operator/sequential-coding` is an offline settings operation namespace,
separate from Founder Save/lookup, not a new agent, permission or auth rule.
Exact original replay authenticates command/receipt before current CAS and writes
nothing; changed bodies conflict. Read-only `admission-show` separates original
receipt from latest generation/admission. Preserve original command/key on loss.

The existing offline owner requires registered stopped-state and operator-held
complete native writer/preparation/process/job exclusion. Agent environments
refuse. Fresh writes need a verified independent checkpointed backup. Complete
readers, exact scoped preservation of all three writable tables plus complete
SQL/identity, verified backup and lifecycle rereads enclose one writer/COMMIT.
TASK-15618 sizes this operator for retained ordinary history with one original
900-second cooperative deadline, a streamed <=32GiB backup hash in 4MiB chunks,
and a 640M-step ownership SQL guard; other reader callers keep 32M. The existing
8192-row/64MiB workflow acquisition limits remain. An INSERT-only SQLite authorizer
and exact expected append comparison protect all original rows and SQL. Replay/
readback need no backup hash. See the [sizing and caller inventory](../../superpowers/specs/2026-11-12-sequential-coding-u9.md#retained-history-repair-task-15618).
The installer/shared whole-database inventory stays byte-identical: its 8192-row/
64MiB/5s ceiling is a known pre-existing production-blocking defect for follow-on. Exit4 retains
COMMIT/rollback/close/delivery/post-publication uncertainty, with no automatic retry.
It installs no schema, creates no marker/run/task or native/remote action, converts
no old Explicit setting and runs nothing at startup. Installation remains the
existing explicit offline schema path. See the
[operator procedure](../../runbooks/sequential-coding-cutover.md).

The original TASK-15616 setup unit was inert. TASK-15624 now supplies the paired
public Coding selector/old-fresh fence described above and updates host setup
copy accordingly. Historical replay, settlement, cancellation, LOCAL Final,
U7/U8, Pause and original budgets retain their owners. Noncatalog V3 fixtures
still require `check_venue_start`.
No live setup/admission/deploy/restart, genuine scratch, V3 extension or effect
recovery is authorized. Source/static and synthetic diagnostics do not establish
operator/native/V3 acceptance; independent review/QA and manager merge remain.
