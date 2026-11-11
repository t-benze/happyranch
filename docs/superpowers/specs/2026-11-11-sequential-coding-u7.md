# Sequential Coding U7: common transport and agent initiation

> Status: current design; U7a/U7b/U7c1/U7c2b delivered in source, including complete Final acceptance; U7d remains.
> Current Source: docs/agent-guides/contracts/reusable-workflows.md

THR-336 / ROOT TASK-15165; TASK-15465, linked retry of pre-edit failed TASK-15462.
Plan rev3 TASK-14461 §U7, R-M2-1, R-M3-3 and R-M3-4 govern. Product authority:
PRD v1.0.4 Shared Artifact `product_lead-2026-11-11-workflow-mechanism-prd-v1.0.4.md`,
SHA256 `96925395de4e44cc5c1570deb512ec843fcfd72c0b3eaeff0423c00b58a07bb5`,
Founder THR-336 msg131 ACCESS-1. Source delivery is not deployment or acceptance.

## U7a assumptions and scope, declared before edits

No schema/table/column/index/migration, dependency or permission change. This
unit adds only existing-Founder-bearer browser GETs, closed projection records,
TS mirrors and provider read hooks. The existing closed StartCommand remains
`workflow-sequential-start@1`; no second command family or write is introduced.
Task sessions have no access through these routes. They retain their original
addressed-input readers and later U7c transport. The managed connector's explicit
allowlist is unchanged: all six new routes are denied there by default. Native
agent-only audiences receive no entitlement. Existing native local browser bearer
access remains the same audience; no new identity, credential or fallback.

Catalog lists only stored `release/happyranch/coding` versions. It does not register
packaged resources on GET or put Draft → Review fixtures in the production catalog.
Exact-version lookup uses the stored definition ID, including a fixture's admitted
version where a run links it. Definition JSON is the original canonical UTF8 text,
with source/definition digests and ABI pins, never a normalized replacement.
Missing sequential storage returns an explicitly unavailable empty catalog/run
list; selected settings/detail reads refuse. No installation or readiness follows.

## Routes and disclosure

All routes are under `/api/v1/orgs/{slug}` and use existing bearer authentication.
Unknown/duplicate queries and nonempty GET bodies refuse. Responses are no-store.

| GET | Projection |
| --- | --- |
| `/workflows/sequential/definitions` | Stored Coding versions; explicit storage availability |
| `/workflows/sequential/definitions/{definition_id}` | Exact immutable version/pins/definition text |
| `/workflows/sequential/settings?definition_identity=…` | Latest saved generation/digest, repo-free defaults/limits/admission; no work allocation |
| `/workflows/sequential/runs` | Authenticated run state/counters, pinned version and real source/carrier/visit links |
| `/workflows/sequential/runs/{run_id}` | Same run, original request/bindings/limits, visits/outcomes/findings, allowed UTF8 output content |
| `/tasks/{task_id}/sequential-workflow` | Same run projection or null for an ordinary/retained task; no invented tasks |

Existing Founder bearer task-result visibility permits the returned authored
content. No task session can use browser routes to widen its recipient set.
Fresh current authority and the complete combined-history oracle precede run
content/link disclosure; current eligible active fixed principals and the saved
immutable repository subject are checked. Current configured origins must still
match the original subject. A denial returns no partial list, task link, request,
output or historical proof material. Unavailable/invalid history or authority
returns409; positive role/subject denial returns403. Native absent task404 precedes
ownership409, including malformed existing ownership. No raw origin, local path,
SID/handle, native proof record, credential or executable Final selection is exposed.

`sequential_views` owns records and projections. It follows the existing SELECT-only
profile/publisher/database read pattern, with no durable admission lease, an owned
BEGIN/rollback snapshot and no DML. One original five-second deadline covers fresh
capture, complete read, projection and closing authority/serialization. The complete
oracle retains its original D2 A/B/C/L allowances; the bounded projection has its
own finite allowance on that same deadline, never retries/reset after refusal.
R-M3-2 per-read limits, original run budgets and cancellation owners are unchanged.

The route module is registered by create_app. Every route has one TS mirror in
`workflowSequential.ts` and an included classification entry. R-M3-4 retains
OpenAPI/classification/TS review parity and an out-of-repo synthetic diagnostic;
Vitest/openapi-coverage.test.ts remain retired. The provider contract and real/mock
implementations expose only reads. Org/selection keys, abort signals, zero retry,
no persisted cache, selection-exit eviction and hiding data during refresh/error
prevent stale cached content from supplying current access. U8 still owns UI.

## U7 implementation units, in order

**U7b — repo-free settings PUT CAS and original-operation lookup (source delivered).** Define the
closed command/receipt compatible with existing storage readers. Freeze one
original operation key/body through uncertain transport, and read its original
receipt separately from a newer current generation. Save preserves admission
and allocates zero work. Allow/disable future admission is a separate supported
setup action; saving cannot enable it. No repository/format/base/scope selector
in setup. Preserve old repo-bearing settings/key recovery with its old reader.

### U7b source (TASK-15482)

No schema/table/column/index/migration or dependency change. New closed canonical
UTF8 `workflow-sequential-settings-put@1` has exactly format, operation_key,
expected_generation (strict integer 0..9223372036854775806), definition (complete
immutable pin), bindings (1..16 ordered role/principal pairs) and limits. Maximum
16384 bytes; duplicate/extra keys, noncanonical JSON, unsafe types and repository,
object-format/base/scope, admission or supplied principal/task/session selectors refuse.
The existing bearer authenticates server-fixed principal `founder`; this is no
new credential, grant or agent transport.

- `PUT /workflows/sequential/settings` accepts only that body, no query. It
  appends one generation with CAS on the latest generation for this org/definition
  identity. First save requires expected_generation=0 and defaults Disabled.
  Later saves copy current admission exactly, including Allowed. No save enables
  or disables admission. Bindings must be complete, independent, currently active
  executable eligible principals in the registered manager's team; limits cannot
  exceed the selected stored definition. There is no repository/access observer.
- `GET /workflows/sequential/settings/operations?operation_key=…` returns only
  the original operation's `workflow-sequential-settings-receipt@1`: org_slug,
  operation_key, original reconstructed command, literal storage receipt and
  original SettingsView. It does not select or label a newer generation current.
  The existing U7a settings GET separately reads current. Original lookup keys
  retain the existing Token grammar, including slash/colon, through query encoding.

The command is losslessly reconstructed from unchanged immutable snapshot and
storage-operation@1 bytes; no new storage family is written. Preserve binding
order and the exact expected generation. Same key/body returns the original
receipt even after later saves; different body refuses before another generation.
The key is scoped to org/server-fixed founder. Existing operator and repo-bearing
Coding histories/readers remain unchanged, without adoption or rewriting.

`sequential_settings` owns this service; the existing router adds two routes and
leaves all U7a GET implementations unchanged. Read-only original lookup and exact
replay use the existing profile/publisher/DB fences, BEGIN/rollback and SELECTs,
with no durable write lease. Absent operation404 precedes existing ownership409;
invalid/foreign bearer has no access. Fresh saves then register the unchanged
native org_settings writer and enter the supported admission writer, rechecking
key/body/CAS/current authority and complete history. Complete target validation,
closing authority and serving-org identity precede COMMIT; COMMIT/lease/native
settlement failures retain uncertainty and never return a fabricated receipt.
One original five-second deadline covers the operation. Each mandatory complete
read retains its OWN unchanged D2 allowances; projection/copy/write work shares
one finite original allowance, without retry/reset. Save allocates zero runs,
tasks, sessions or jobs and changes no admitted run pins.

TS has one mirror per route and uses the existing explicit ordinary-token option
for one PUT even on401. Its deep-frozen canonical command precedes awaits. The
provider's QueryClient-local org fence survives pane unmount and A→B→A, with no
persistence or locale input. Pending/uncertain operations reject another send;
404/error/mismatched original lookup retains custody. Only a matching original
receipt resolves uncertainty. A receipt never populates the current-settings
cache; an independent invalidated U7a read reacquires current. Known pre-write
validation/CAS conflicts may release the send fence; history/persistence failures
remain uncertain. U8 still owns rendered setup/draft states and bilingual UI.

Both routes are browser-included in classification with OpenAPI/TS/provider
parity. Existing managed-connector policy denies them; native agent-only audiences
and agent sessions gain no entitlement. Existing auth boundary is unchanged.
Retired Vitest/openapi-coverage remains retired. No admission-changing setup action
is required by this Save unit or exposed here; a separately authorized supported
action remains separate source work, and live admission stays Founder-reserved.
No fresh start, task-session preflight/Final mount or Final-success admission.

Diagnostics are disposable OUT-OF-REPO SYNTHETIC route/writer/reader observations
with explicit authority/native scaffolding, not genuine persistence/operator,
native/V3/V5/M4/access/merge or activation acceptance. Required cases/evidence and
NOT RUN limits are recorded in TASK-15482 handoff. No repository tests added.

**U7c — selected task-session preflight and Final transport.** Subject-only
repository/base/scope/SHA1 preflight; no four-role duty fields or access observer.
Reject browser/foreign/recovery sessions and derive native task/principal/SID
from actual current binding. Mount the U5b2b1 local adapter only in the genuine
selected live Final-manager session, with compatible U5b2b2 callback reader,
current authority, U6 cancellation and original native ownership. Decide and
justify guarded-repository-merge@1 success admission in that coherent unit.
Until then **Final success stays Held**, with existing rollback-then-business-Held.
No daemon GitHub/network I/O, second CLI OrgState or job/recovery Final executor.

**U7d — ordinary workflow_start completion consumer.** Agent-prepared unchanged
StartCommand is consumed only by its real ordinary source completion. Principal,
source task, SID and positive INTEGER result are server-derived. No human/browser
start endpoint. Keep public fresh-start attachment OFF until U9 atomically fences
old fresh starts; no flag shortcut or historical engine fallback. Existing original
source return and thread association remain; notifications are excluded.

## Preserved owners and evidence limits

Historical workflow_coding_* families/routes/readers and old start@1/@2 behavior
remain unchanged; U9 owns cutover. TASK-15250 original Pause generation,
TASK-15272/15275 receipt-write uncertainty, U6b2a sequential-only proof,
Option B ordinary/document UNSETTLED and R-M3-2 remain with their owners.
ACCESS-1 means daemon current authority only. Effective access is proven solely
by each selected agent's own live operation; failure is denied versus unverified
BLOCK → Held. Agents share one OS user and gh credential; no isolation or lasting
grant is claimed. U4a/U4b2 local clone preflight is unchanged.

Out-of-repo synthetic diagnostics exercise actual routes/readers/projections
with explicit authority/native scaffolding, including denied/foreign disclosure,
404-before409, zero GET DML and no start/finalize. They are not native/V3/V5/M4,
real-agent access, genuine merge or acceptance evidence. Exact-head local CI is
a durable job; hosted checks are observed once, with no wait. Independent code
review, scoped QA and manager guarded merge remain required. Python units and
Vitest RETIRED; integration SKIPPED THR-243seq42; E2E untriggered.

All prior failed/REQUEST_CHANGES/QA-FAIL histories and consumed allowances are
preserved. V3 ends2026-11-23T08:48:38Z; extension, live settings/admission/roles,
installation/migration/deploy/restart, genuine Coding scratch and effect recovery
remain Founder-reserved. PR1252 remains held. F-1=(i), Decision1=B (48KiB JSON),
Decision2=1, SB-1=(a). Merged != deployed.


## U7c1 selected task-session transport (TASK-15494)

This source unit mounts two SELECT-only GETs under `/api/v1/orgs/{slug}`:
`/workflows/sequential/repository-preflight?session_id=…` returns only the
original committed RepositoryChangeSubject (repository identity/key/origin digest,
SHA1/base/scope). An unbound subject refuses409; this read does not resolve/bind
one or release editing. U4a/U4b2 retain resolution, positive-COMMIT binding and
clone metadata ownership. There are no four-role duty/access fields.
`/workflows/sequential/final-manager?session_id=…&change=…` validates the bounded
canonical code-change declaration against the selected full settled history and
returns the existing closed Final selection to its OWN live manager only.
The declaration is untrusted; it grants no repository/merge capability.

Both routes require the existing CLI surface and exact live ordinary session
binding; supplied SID only looks up that binding. Task/principal/current SID and
native invocation/workspace are server-derived. Bearer, browser Origin, foreign,
recovery, ended/replaced and nonselected sessions refuse. Native absent404
precedes ownership409. Native agent-only and managed-connector audiences are
excluded: native SurfaceAllowList explicitly denies these two new GETs; the
managed allowlist is unchanged/default-denied. No TS/browser mirror or
agent-permission/credential change.
No-store, strict empty body/query, current ACCESS-1 authority and original native
owner/cancellation bookends precede disclosure. The profile/publisher/DB/binding
read fences own BEGIN/rollback with no durable admission lease or DML. Full mixed
oracle and original per-read D2 allowances share one five-second deadline with
one finite projection allowance; refusal never renews it.

`happyranch workflows sequential repository-preflight --org ORG --session-id SID
--json` reads that subject. `happyranch workflows sequential finalize --org ORG
--session-id SID --from-file ABS --json` consumes a closed canonical <=49152-byte
workflow-sequential-finalize-command@1 with exactly format, operation_key,
repository_path and change (the whole selected code-change declaration). It
contains no supplied task/principal/native pin. Keep original key/body after
transport uncertainty. This LOCAL command obtains fresh HTTP selections at every
existing U5b2b1 adapter reselect; no second CLI OrgState, daemon Git/gh/network I/O,
job/recovery executor or fresh start is introduced. The original-task local fence,
intent, expected-head non-admin guard, separate remote read and recording-fault
uncertainty remain unchanged across key/cwd/path variants. It exclusively saves
the compatible callback.json before printing the canonical generic callback
STRING. It does not call report-completion. The genuine manager must use that
string in its own FINAL callback, preserving causal BLOCK and empty content/claims
for uncertain/failed receipts. Lost output/persistence never authorizes reissue.

The original post-positive-outer dispatcher now invokes U5b2b2's complete/selected
callback reader before successful Final composition, retaining denied versus
unverified and cancellation/Held owners. It shares the enclosing deadline,
restores the enclosing SQL hook afterward, and still reaches original
rollback-then-business-Held on validated success. Negative callbacks retain their
original path. Historical Coding and U7a/U7b routes/semantics are unchanged.
Before the added Final-path carrier decode, the dispatcher prepays
`48 * 4 * len(summary) + 512` bytes and `len(summary)` traversal steps on its
SAME original prefix allowance. The native result preflight already bounds the
string; four bytes per character bounds UTF8, and carrier metadata plus decoded
domain JSON has fewer tokens than carrier characters. This covers the new
encoding/canonical/base64/domain parse and copy work before it starts, without
encoding just to size the reservation. Exhaustion propagates as the original
reader refusal with no committed effects, never malformed-output business-Held.
The compatible callback reader retains its original R-M3-2 limits on the shared
deadline; no cap, allowance, retry or historical decoder is changed.
The dispatcher checks that SAME original deadline at the callback-reader handoff
and before classifying its refusal. Original deadline exhaustion propagates as
`workflow_seq_read_deadline_exhausted`; the callback reader's own read-exhausted
refusal also propagates unchanged. Neither path records a business-Held overlay
or commits DML. Validated selected success still rolls back to business-Held.
At the mounted callback reader's refusal catch, genuine exhaustion on the
SAME original allowances propagates even when a nested allocator reports a
generic history error. The mounted refusal is
`sequential_final_observation_read_exhausted`, without a business-Held overlay.
R-M3-5 permits narrow protected-source exceptions at original allocator checks:
failed reservations carry a typed causal signal with the original allowance,
dimension, requested and remaining amounts. Existing outer exception classes and
code strings remain unchanged, including retained copy shortfalls before any
counter changes. SQLite progress retains its actual refusal for causal chaining.
The mounted catch walks cause/context links across D/P/A/B/C/L/R; it never infers
exhaustion from counter signs or traceback frames. Standalone calls without an
enclosing deadline and genuine malformed history retain their classifications
and persisted bytes. No cap, original deadline, allowance or retry is renewed.
Sequential-start recognition retains genuine exhaustion as the cause of the
caller's already handled first error before returning its original Boolean
fallback. Both queued and central-metadata rethrows therefore preserve it;
ordinary unmatched recognition still returns False with its original error.

**Final success remains Held; U7c2 is implementation remainder.** Transport and
validated observation do not implement a Final capability witness, recipients,
verified-check composition or complete output-history acceptance. U7c2 must add
those compatible consumers and same-transaction publication/return validation
before admitting guarded-repository-merge@1 success; it is not a flag change or
verification-only task. This split is the brief's permitted first coherent unit,
not product/external-contract or authority expansion. No positive admission or
native/V3/V5/M4/genuine access/real-merge acceptance is claimed. Shared OS-user/gh
custody and PRD v1.0.4 / Founder msg131 ACCESS-1 remain controlling. Public fresh
start stays OFF until U9. OpenAPI/classification parity and OUT-OF-REPO SYNTHETIC
actual-route/local-mount fake-effect diagnostics accompany the source; no repo
tests, Vitest restoration, integration/E2E/live operation or merge occurs.

## U7c2a unmounted Final witness consumer (TASK-15549)

**Split declared before edits:** this unit supplies the compatible Final witness,
recipients and verified-check composition only, without a production caller.
Complete output-history acceptance and same-transaction publication/return
validation remain implementation in **U7c2b**. They must land together with the
mount. Final success stays original rollback-then-business-Held; public fresh
start stays OFF until U9. This is the brief's permitted consumer-first split.
Admission remains withheld because a valid observation and transient witness
alone do not authenticate a persisted Final output through complete replay or
prove acceptance/transition/return publication in the original transaction.
No positive admission, successor or caller-return acceptance is claimed here.

`sequential_coding_final_output.build_final_witness_uncommitted` runs only inside
the caller's original authority/teams/DB locks and transaction. It calls the
existing U5b2b2 reader itself, including complete mixed history, exact selected
INTEGER callback, original successful host/positive outer, current ACCESS-1
authority, immutable subject, mandatory independent APPROVE/PASS, original Pause
and cancellation checks. It accepts no caller-created validated-observation
object or trusted Boolean. It returns the existing internal `CodingWitness`,
never an OutputRef, accepted result, storage write or executable Final selection.

The supplied original content digest must equal the canonical ENTIRE validated
observation. Original carrier/terminal/outer digests and result identity must
match; definition/start/input/CAS/epoch pins must match the same selection.
Recipients reuse U4b3's producer plus all declared `current.slot` consumers,
including optional inputs, in sorted role order under the original fixed
bindings. Each node completion check gets a deterministic evidence digest of
the internal `workflow-sequential-coding-final-witness@1` preimage: org/run/full
definition pin/start/visit/node/role/epoch, task/principal/SID/INTEGER result,
carrier/input/slot/contract/content/selection digests, addressed role/principal
pairs, original terminal/outer digests and the check. The content digest binds
the immutable change, saved intent and both literal lints, successful original
command and separate remote MERGED/full-commit/aware-time observation. These
are provenance declarations; ACCESS-1 leaves effective access to the selected
agent's own operation. No daemon remote observer, credential or access grant.
Verified checks are exactly settlement followed by the node's existing checks.

The caller supplies its SAME original `_ReadBudget`; this consumer constructs
none. The existing nested reader retains its unchanged per-read R-M3-2
allowances on that original deadline. Entry, handoff and final-return checks
retain the deadline. Before the new model dump/canonical copy/hash, reserve
`48 * 49152 + 512` bytes and 49152 traversal steps on the caller allowance.
Before preimage construction reserve 262144 bytes and 512 steps; before EACH
bounded evidence copy/check/hash reserve `48 * 16384 + 512` bytes and 512 steps.
Node/binding/recipient walks also charge the same allowance before their work.
No cap rises, reset, retry, fork or replenishment; conservative prepayment can
refuse a legitimate large history. The consumer has no refusal catch or Boolean
fallback. Every original read exhaustion escapes unchanged, with no Held writer,
DML or COMMIT, and the original SQL hook is restored even on refusal. Genuine
malformed history keeps the existing unverified classification. Standalone
readers, exception classes/strings, historical bytes and action code are unchanged.

| Owner | Consumer / preserved boundary |
| --- | --- |
| New Final witness consumer | No production importer/caller; actual reader and witness exercised only by out-of-repo synthetic diagnostics. |
| U7c1 mounted reader and `_advance` / `_compose` | Byte-unchanged. Validated success still rolls back, then records finite unavailable-output Held with NULL selected result and no Final output/checks/successor/transition/return. |
| Complete/selected history, engine acceptance/return, U6/native Pause and LOCAL action/receipt owners | Byte-unchanged. U7c2b must finish compatible history and same-transaction admission before mounting. |

No schema, auth/permission, dependency, route, command, settings or external
contract change. TASK-15250 Pause generation, TASK-15272/15275 command=None/no
reissue, U6b2a sequential-only, Option B ordinary/document UNSETTLED, all prior
failures and spent allowances remain. Exact B/H, changed-symbol/caller inventory,
protected byte/AST comparison, exhaustion-site inventory, synthetic positive
witness/negative/refusal evidence, actual mounted Held control and exact-H
local-CI job receipt are in `dev_agent/output/TASK-15549/handoff.md`. All synthetic
facts use disposable native/authority scaffolding and fake effects, not genuine
access/merge/native/V3/V5/M4 acceptance. Python units/Vitest RETIRED, integration
SKIPPED THR-243seq42, E2E untriggered. Independent review, scoped QA and manager
guarded merge remain; merged is not deployed. U7d/U8/U9 and all Founder-reserved
operations/holds remain separate.

## U7c2b complete Final admission (TASK-15564)

This section supersedes the earlier U7c1/U7c2a withheld-admission status. A
validated `guarded-repository-merge@1` success is admitted through the existing
post-positive-outer dispatcher. This is provenance acceptance of the original
agent-owned operation, not a daemon observation of GitHub or permission to repeat
it. Public fresh start remains OFF until U9; U7d/U8/U9 remain separate.

The mount, complete-history acceptance and original-transaction publication
validation ship together. The original authority/teams/DB locks and admission
transaction enclose witness composition, accepted output/verified checks, the
existing engine transition/successor, terminal return, caller-field projection
and the complete mixed target oracle. Only that COMMIT precedes queue/caller
notification. A late write, replay or validation failure rolls the whole staged
publication back. No new transaction, schema, family or trusted Boolean is used.

`sequential_coding_final_history.validate_final_output` is called by complete
run replay on its existing allowance. It authenticates the entire canonical
observation against the original INTEGER callback and successful terminal/outer
proofs, immutable subject and selecting event/CAS, original native root/request/
handle, mandatory independent settled development/APPROVE/PASS and the original
retained current-slot prefix. Final's addressed inputs alone are insufficient.
It recomputes exact sorted recipients, verified checks and deterministic Final
receipt digests from the original fixed principals, without current-role lookup
for historical attribution. No future visit contaminates the contributor set.
The shared pure receipt derivation changes no preimage or persisted family.

`validate_final_publication` reads complete mixed history before COMMIT, then
validates the accepted result and terminal run-return obligation, exact original
source/carrier completion fields, current fixed binding/origin and closing
ACCESS-1 authority. Duplicate delivery validates persisted publication, writes
nothing and emits no duplicate notification. It does not re-enter the reader
for an unaccepted callback or reissue the LOCAL effect. Malformed/uncertain
proofs refuse; current denied versus unverified authority remains distinct.

New copies, parses, model dumps, hashes and traversals prepay the same caller
allowance before work. Historical observation parsing reserves `48*49152+512`
bytes and one traversal per actual content byte (a token upper bound); carrier
decoding reserves `48*4*len(summary)+512` bytes and `len(summary)` traversals.
The live witness retains its original fixed prepayment. Envelope composition
prepays `48*65536+512` bytes and1024 traversals (one output,32 findings and8 receipts have fewer than512 model/container nodes). Whole-target reads each retain
their OWN unchanged D2 A/B/C/L allowances on the SAME original operation
deadline (R-M3-2). SQL-hook custody is restored at reader handoffs. No cap,
deadline, attempt, retry or spent allowance is renewed. Genuine pre-write
exhaustion escapes with zero DML/no COMMIT; a late target-validation refusal
rolls back already staged DML and publishes nothing. Cooperative deadlines do
not establish hard OS timing.

Only `sequential_history` changes within the existing protected13 radius; engine,
selected validator, historical families, U6/native Pause and LOCAL action/receipt
owners remain byte-identical. `_compose` is unchanged; `_advance` owns the Final
branch. Original Pause generation, cancellation-before-action, command=None/no
reissue, U6b2a sequential-only and Option B ordinary/document UNSETTLED persist.
No daemon network/GitHub I/O, observer, credential, permission, dependency,
installation or live operation is introduced.

Out-of-repo SYNTHETIC diagnostics and exact B/H inventories/commands are recorded
in dev_agent/output/TASK-15564/handoff.md. Fake native/authority/effect fixtures
are not genuine access/merge/native/V3/V5/M4 acceptance. Units/Vitest RETIRED,
integration SKIPPED THR243seq42, E2E untriggered. Exact-head local CI uses a job;
hosted checks are observed once. Independent review/scoped QA and manager
guarded merge remain. All prior failed histories, spent allowances, Founder
reservations, V3 end, PR1252 hold and PRD v1.0.4/msg131 remain unchanged.
