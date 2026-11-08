# Reusable workflow contracts

Founder THR-336 message 85 approved TASK-14461 plan revision 3 under manager
root TASK-14478. TASK-14483 delivers **U1a**, the records/validator half of U1,
for F1/F3/F6 and AC1/AC5/AC8. U1b must add the common reducer and data-driven
brief renderer in a separate PR. This is source delivery, not M1 acceptance.

`runtime/workflows/sequential_contracts.py` owns closed syntax and immutable
compiled records. `sequential_definition.py` owns pure canonical parsing,
definition compilation, graph validation and start completeness checks.
Neither module allocates tasks, reads resources, registers releases, performs
I/O, calls Coding progression, or has a production producer caller. The
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

Interpreter and capability pins reserve the U1b/later-unit interface; they do
not advertise an installed engine or working capability implementations.
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
definition/input-selection digests, declared outcome, output references,
attributed findings and capability receipt references. Output refs fix slot,
typed contract, epoch, producer visit/task/SID/positive INTEGER result and
digest. produced/APPROVE/PASS/merged require outputs. Other outcomes require
attributed findings and carry no successful output claim. Duplicate output
slots/capability checks refuse. A reference or unsigned report is no trusted
receipt; syntax parsing does not validate content, provenance, selected visit,
current subject, settlement, authority, or whether the node permits the outcome.

`parse_transition` accepts canonical `workflow-sequential-transition@1`: run
and definition pin, source visit/INTEGER result and result/output digests,
node/outcome, prior CAS/event digest, before/after counters, invalidated output
refs, selected closed edge and successor visit/task or null. A next edge needs
its matching successor; other edges cannot supply one. Each counter snapshot
requires `epoch=content_returns+1`. U1b must deterministically verify counter
deltas, selected edge, invalidation and result identity; a future owning writer
must authenticate persisted history, CAS, one-time consumption and genuine
fresh task/visit allocation. Parsing grants none of those effects.

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
`coding_progression.py` remain byte-identical. No storage/schema, task producer,
route, settings, admission, CLI, native enforcement or old-reader behavior
changes. Existing admitted histories keep their original family. Startup,
installation, cutover and live enablement are outside U1a.

No tests or behavioral harness are added. Python units remain RETIRED, general
integration SKIPPED under THR-243 seq42, and Product E2E is not triggered.
The maker invoked the pure compiler on all four canonical example contents;
all were accepted. This narrow data-validation smoke establishes no malformed
input coverage, common reducer behavior or service evidence. Negative compiler
cases, shared returns, invalidation, rendered briefs, real-task progression and
M1/V3 acceptance remain **NOT RUN/open**. Lint/static results
in the PR are source evidence only. Independent full review and scoped QA must
assess this boundary honestly before manager guarded merge.
