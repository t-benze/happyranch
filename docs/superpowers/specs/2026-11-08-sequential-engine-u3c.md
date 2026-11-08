# Sequential engine U3c — inert lifecycle source

2026-11-08 · TASK-14681 · engineering_manager ROOT TASK-14640.
Append-only source record; not installed/runtime or V3 acceptance.

Founder THR-336 msg85 approves U1–U9 SOURCE under TASK-14461 plan rev3.
Msg102 accepts msg99/100 Decision 1 = B. Current delegation continues partial
TASK-14677, including the TransitionRecord-only ruling and ROOT14640's
`u3c-evidence-sizing-ruling.md`. Failed predecessor roots/tasks and spent
allowances remain history. Base a23390c3e279e09e41d42ccf89d9aebf0f0f7166 contains
U3b 54861b02ced4f7fa86e55aca623775273a491138.

## Boundary and assumptions

Only five paths change: sequential_engine.py, sequential_run_validation.py,
TransitionRecord in sequential_contracts.py, reusable-workflows.md, and this
new spec. U3b AdmissionIds/admit_uncommitted/activate_uncommitted are unchanged.
The complete reader, reducer, schema, original caps, native ownership/auth/
permission machinery, callbacks, routes, scripts, dependencies, hooks and CI
remain unchanged. No caller is attached. Writer methods are synchronous,
caller-transaction-owned, with no commit, await, launch, file/network or actual
notification effect. The future adapter owns complete callback, access,
current authority and positive original native/outer-settlement authentication.

Assumptions are explicit:

- Accept receives exact output `content: bytes`, declared contract, canonical
  envelope, actual INTEGER result/task/session binding and distinct verified
  checks as explicit arguments. It never extracts content from a summary.
- Native session binding precedes engine session binding, in the same genuine
  caller-owned admission writer. U3c creates no session or launch proof.
- Structural fixture result rows use unchanged db._insert_task_result. These are
  fixture seeds, not real callbacks, domain receipts or settlement evidence.
- Incremental validation relies on earlier validated immutable rows. It does
  not promise complete detection of out-of-band older-history corruption.
  The unchanged full reader remains the independent oracle and retains all
  existing inventory/work refusals.
- Sizing below is for the approved Coding v2/J and Draft/Review/I fixture
  definitions at their declared limits, not an assertion that every arbitrary
  definition up to the syntax's 1 MiB/64-visit ceilings fits SB-5. New definitions
  still require their own bounded sizing; identity selects no engine branch.
- PRD amendment remains product_lead/THR341's responsibility; no PRD publication
  or live policy acceptance is inferred from this source change.

## Decision 1 = B and typed refusal

`JSON_OUTPUT_LIMIT = 49152`; `DOCUMENT_OUTPUT_LIMIT = 1048576`.
`check_output_size(contract, content)` checks raw byte length only before
write/decode/digest. Bounds are inclusive. Larger input raises
`SequentialRefusal(code='sequential_output_limit_exceeded')`, saves nothing and
cannot succeed or advance. Every non-document contract uses the JSON bound.
The accept scope repeats the gate using an indexed length-of-BLOB read before
payload acquisition (including the original 1 MiB cell guard), then validates
only the accepted output. Transition never acquires output content.

**F-1 remains open:** without an accepted result, unchanged reader/reducer/schema
cannot represent a durable sequential hold with an output-limit reason. The
refusal is zero-write and cannot fabricate an accepted result, event or success.
A native terminal with no accepted result can leave sequential active/running
as stalled; the cancel fence is the existing exit. Owner resolution is required
before U3d-1; this unit does not choose a new persistence model.

## Evidence engineering contract

Evidence is deterministic runtime-built content, not agent/user node output.
ROOT14640 resolves the previous SB-5 stop as an engineering writer boundary:

- `EVIDENCE_CONTENT_LIMIT = 16384` raw stored bytes.
- `check_evidence_size(raw)` uses length only, before write/decode/digest, and
  raises the same typed refusal with `sequential_evidence_limit_exceeded`.
  U3c writes no evidence. Every future U3d-1 evidence writer must call this gate
  before insertion; it must not copy or weaken the check.
- `scope='evidence'` accepts at most two distinct IDs per call. The existing
  8192-ID request guard stays unchanged. The new bound is scope-local.
- Before content acquisition or unchanged `_auxiliary` digest/decode, each row's
  raw stored byte length is read by primary key. Stored 16,385-byte content or
  three IDs gives `sequential_evidence_limit_exceeded`. Schema/content/ownership
  authentication then continues through unchanged `_auxiliary`.
- The original 1 MiB cell guard, 64 MiB / 8192 rows / 65536 steps / five-second
  reader caps and SB-5 comparator are unchanged.

The historical 65,536-byte dense JSON evidence probe used 36,936 validator steps
(TASK-14677), exceeding 32,768. Its unchanged decode alone cost 32,768 steps.
It motivated this bound; it is no longer a declared-limit evidence case. One
16,384-byte node-dense row now costs 12,362 validator steps; two cost 20,569 at
admission and 21,909 after later progress. Stored 16,385-byte rows and three-ID
calls refuse, with rollback observed. The two-row late probe is intentionally
rolled back after measurement; no combined full-reader acceptance is claimed.

## Writers, replay and incremental correctness

Session binding updates only the selected queued visit to running, matching its
already-owned native task session. Acceptance binds the native result quartet,
verified-check bytes and at most one output. Negative/return outcomes require
empty content/recipients and create no output. Exact replay validates the
original accepted visit, even when a later visit is active or the run ended.
Different identity/content/checks/recipients refuse without sequential changes.

Transition reconstructs counters and current selections from immutable prior
transition records and output metadata. It parses only the selected result,
calls the unchanged reducer, persists the exact outcome/edge/counters, and
allocates a pristine native successor plus v2 ownership/outbox only on next.
Unknown bounded outcome tokens persist as literal holds. The only protected
contract amendment changes TransitionRecord.outcome from OutcomeName to Token
and permits non-registry tokens only with HoldEdge. All declared registry
outcomes retain their prior shape rules; non-registry next/terminal/fail refuse.
Return/visit exhaustion reserves zero successors and never replenishes counters.

Transition replay looks up the stored operation event, authenticates its
original source result/visit/CAS, and validates that selected event rather than
the latest event. It returns original event/visit/task/outbox IDs after later
progress, terminal completion or cancellation. Reused operation/result identity
with changed input refuses categorically. A replay does not allocate a task.

Return appends returned plus one run_return outbox record and closes the
source/carrier task rows. No notification is sent. Cancellation appends a fence
from active/held and clears selection. Native process interruption, outer
settlement, orchestration publication and notification delivery are later units.

Every writer ends in its own scope: session, accept, transition, return or
cancel. Admission is unchanged; evidence is the extra selected scope for U3d-1.
Accept and transition use separate fresh original budgets. Public optional
visit_id/event_id selectors are read selectors for the original operation,
not authority or an alternate mutation target. All SQL mutation predicates
name the selected run/visit/task/event. Existing immutable triggers and deferred
FK checks remain. Complete independently built selected schema comparison,
settings pins, event/counter/selection invariants, selected ownership/native
point reads and auxiliary outbox/evidence checks precede COMMIT. Each writer
must escape refusal out of the caller writer so all effects roll back.

## Measured and computed sizing

Half-caps remain **33,554,432 bytes / 4096 rows / 32768 traversal steps /
2.5 seconds**. Any measured scope or computed declared-limit bound over any
half-cap is STOP, without changing limits/comparator. These are reservations,
not RSS, and cooperative time observations, not hard real-time guarantees.

Disposable observations use the real Database, merged org initializers and
installer, real authority capture/admission_writer, and unchanged full reader
on a separate connection. No proxy, subclass, monkey-patch or product test seam
is used. External profiling reads original budget objects; SQLite's native
trace/EXPLAIN supplies query plans. No retired Python tests are imported/run.

Measured maxima below take each dimension's maximum over both fixtures and
named boundary/late-progress/replay cases. They aggregate the engine and its
selected validator as a stricter diagnostic; no two scopes share a budget.

| Scope | Aggregate bytes | Rows | Steps | Seconds |
| --- | ---: | ---: | ---: | ---: |
| session | 11,945,379 | 1538 | 5633 | 0.050 |
| accept JSON, 49152 + 65536 envelope | 19,780,627 | 1547 | 30820 | 0.074 |
| accept document, 1048576 | 14,324,446 | 1506 | 4341 | 0.051 |
| transition, no output payload | 24,918,810 | 1670 | 8275 | 0.067 |
| return | 12,081,006 | 1546 | 5815 | 0.048 |
| cancel active/held | 11,697,098 | 1529 | 5171 | 0.056 |
| evidence, two 16384-byte rows | 12,268,212 | 1443 | 21909 | 0.060 |

Computed conservative fixture envelopes include the maximum 16,384-byte
request with six-byte JSON escapes, 65,536-byte result envelope, fixed compiled
pins, one output, two evidence rows, two return invalidations, maximum token
widths and the declared 16-visit ceiling. The measured longest Coding path uses
10 visits with both QA returns consumed. Padding to 16 adds at most six ordinary
visit/event/output/outbox closures; the two return closures are already present.
Counting the bounded column reads, record traversals and auxiliary fields gives
at most 256 additional steps/ordinary closure in accept. Transition counts its
repeated history passes separately, at up to 512 additional steps/closure
(8275 + 6×512 = 11347, below its 12000 ceiling). JSON decode contributes exactly
24576 steps at 49152 bytes. Evidence adds 8192 decode steps per 16384-byte row.
String padding increases reserved bytes without increasing JSON node traversal.
64 KiB result parsing is structured (one output, at most 32 findings/eight
capability refs), not arbitrary node-dense JSON.

| Scope | Computed byte ceiling | Row ceiling | Step ceiling |
| --- | ---: | ---: | ---: |
| session | 16 MiB | 1800 | 7600 |
| accept JSON | 24 MiB | 1800 | 30820 + 6×256 = 32356 |
| accept document | 24 MiB | 1800 | 7800 |
| transition | 30 MiB | 2100 | 12000 |
| return / cancel | 16 MiB | 1800 | 7800 |
| evidence | 16 MiB | 1800 | 23700 |

Byte ceilings cover the raw-row 1×/copy 2× reservations and repeated 48× model
parses (transition has engine plus two validator history passes); metadata-only
old outputs do not pay content decode/copy costs. The transition ceiling also
includes two 64 KiB selected-result parses. Row padding is below 30 reads per
extra closure. Wall-time extrapolation at this finite fixture envelope is below
0.2 seconds on the observed host; only actual measured time and cooperative
checks are evidence, not a platform latency guarantee. Arbitrary larger source
definitions and real callback/domain work remain outside this sizing claim.

## Observations and limitations

Both fixtures: positive next/terminal and return commits; unknown-outcome durable
hold with run/visit held and event_count=2; cancellation active/held; two returns
consume epochs then exhausted return holds with zero successor. Lower max_visits
one likewise holds with no new visit. Every declared node/outcome is covered by
the positive, return and finite alternate-outcome observations; 36 registry
parser edge combinations per fixture remain accepted. Non-registry next and
terminal parser cases refuse. Older accept/transition replay returns original
IDs after later terminal/cancel progress; conflicts and negative-result changed
content refuse. Simple committed flows report storage readable on the unchanged
separate oracle connection.

49152 dense JSON and 1048576-byte documents accept. 49153/1048577 raw input
refuse with zero sequential DML; caller-authority bookkeeping is not a sequential
write. Over-limit inserted outputs are caught by the accept scope, and inserted
16385-byte evidence by evidence scope, with exact rollback. Bad output digest,
mutable counter drift and attempted immutable output UPDATE refuse/roll back.
U3b negative admission regressions remain refused: incomplete/independent role
bindings, Disabled/stale settings, stale authority, mismatched result,
noncanonical start and counter tamper. No audit rows are added.

EXPLAIN on actual traced writer SELECT/WITH statements shows indexed SEARCH
for sequential/native/ownership data; metadata schema/pragma scans remain the
existing comparator. The evidence length check is a PRIMARY KEY SEARCH. The
collector's own diagnostic snapshots are excluded from writer trace: an early
collector mistakenly included those scans and was corrected without source
changes. Another early tamper probe hit the unchanged immutable UPDATE trigger;
corrected probes insert invalid rows in a fresh caller transaction. Both actual
failed diagnostics remain in task evidence rather than being called PASS.

8193 unrelated ordinary tasks/marks leave selected writer accounting unchanged.
The full oracle refuses its existing whole-inventory row cap there. The long
10-visit Coding probe also reaches the unchanged reader row cap; its selected
writer costs are measured, but oracle readability for that history is **not
established**. These existing Decision2 limitations are explicit and do not
alter SB-5, the reader or its comparator.

Structural observation is **not behavioral PASS**, V3 acceptance, a real
callback, a positive settlement, a repository merge or live operation. General
integration is SKIPPED THR243seq42, Python units RETIRED, Product E2E not triggered.
Final-head local boundary/web lint and hosted Source Boundary Lint + Web Lint
(Node24) receipts belong in the PR/task handoff, never inferred from this spec.
Independent full review and scoped QA at the immutable final head, followed by
manager guarded merge, remain required. U3d/U3e/U4–U9, F-1, Decision2 and actual
runtime/Founder acceptance remain UNVERIFIED/unresolved here.
