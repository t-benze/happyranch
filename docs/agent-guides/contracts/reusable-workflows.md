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
refs, selected closed edge and successor visit/task or null. A next edge needs
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
included in wheel packaging without a catalog reader or selector. The three
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

TASK-14536 under replacement root TASK-14531 adds the records/standalone-reader
boundary of U2a. This is SOURCE-INERT: no production importer, producer,
allocator, route, startup SQL or installer is attached. It does not close U2,
M1, AC5, AC8 or AC12. The remaining U2a reader integration must precede U2b
installation and U3 producers.

`runtime/infrastructure/workflow_sequential_schema.py` owns a separate version1
marker and nine tables: definitions, settings snapshots, settings operations,
runs, visits, outputs, evidence, events and outbox. Definitions, settings,
operations, outputs, evidence, events and notifications are immutable, with
UPDATE/DELETE refusal triggers. Runs retain immutable admission pins; visits
retain immutable node/task/input pins and an accepted result cannot be replaced.
Current lifecycle fields remain with their future writer. All result references
are genuine positive INTEGER native identities, never operation IDs. Unique
source/result/start, visit ordinal/predecessor and accepted source visit/result
constraints select one operation; a partial unique index permits one selected
visit per run. The complete reader additionally checks the active pointer and
rejects committed provisional runs. No table extends frozen document/Coding
state grammar. No index is placed in an existing startup executescript block.

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
authenticate; content/capability policy remains with U4/U5.

The separate `workflow_sequential_ownership.py` companion reference keeps all
original columns and v1 kinds, and adds explicit v2 `seq_source_waiting`,
`seq_carrier`, `seq_visit` marks. The literal view appends three relations to
the original branches unchanged. Complete inventory rejects missing/conflicting
marks; a selected v1 mark still uses the existing scalar reader. There is no
source adoption, mark insertion, table rebuild or native task encoding change.
The actual explicit companion upgrade belongs to U2b; adoption belongs to U3.

**Remaining reader integration:** on legacy-only storage the standalone reader
uses the existing complete history reader. On combined storage it validates
new history and complete legacy structure, but does not yet compose all original
document/Coding history validators over the extension. Existing production
readers still refuse installed new objects. A follow-up U2a source unit must
compose those validators without weakening their original recursive structural
checks or routing/scalar ownership contracts before U2b installation is usable.
No installer, install/reopen/backout result or compatibility safety claim follows
from this boundary. [Draft installation/backout contract](../../runbooks/sequential-workflow-storage.md)
is UNVERIFIED. V3 and actual runs remain NOT RUN; old bytes and historical
receipts remain unchanged. U4–U9 remain held on the separate U4.0 NO-GO.
