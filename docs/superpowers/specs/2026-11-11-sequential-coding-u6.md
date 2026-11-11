# Sequential Coding U6: cancellation and mixed-history closure

> Status: current design; U6a/U6b1 and U6b2a inert interfaces implemented
> Current Source: docs/agent-guides/contracts/reusable-workflows.md
> Notes: U6b2b cross-family writers, owner aggregation and attachment remain required.

THR-336 / ROOT TASK-15165; maker TASK-15287. Fetched base B is
`dc35c34ca907d6094cc79654f6f7b5be7476fe82`. Plan rev3 TASK-14461 §U6
and acceptance AC8/AC9/AC12, R-M2-1 TASK-15144 (“U6 unchanged”), PRD v1.0.4
Shared Artifact `product_lead-2026-11-11-workflow-mechanism-prd-v1.0.4.md`
SHA256 `96925395de4e44cc5c1570deb512ec843fcfd72c0b3eaeff0423c00b58a07bb5`
and Founder msg131 ACCESS-1 govern. [U4](2026-11-11-sequential-coding-u4.md),
[U5](2026-11-11-sequential-coding-u5.md) and
[D2](2026-11-09-sequential-history-reader-d2.md) retain their owners.

## Split declared before editing

Deliver **U6a**, a SELECT-only mixed-history interface, before mounting any new
producer. **U6b remains implementation**, not a verification-only remainder:
compose the existing Founder task cancellation/cascade writer with mixed
ordinary/document/old-Coding/sequential owners and native root-tail consumers.
The existing ordinary-ancestor guard and direct selected-run cancellation stay
unchanged. Mixed cancellation still refuses; there is no new executable path.
U5b2b2 callback-observation validation/mount and U7 task-session transport remain
separate. Final success stays unavailable-output Held. No new endpoint, schema,
dependency, credential, authority/permission surface or live operation is needed.

## U6a interface and ownership

`read_sequential_history(..., task_id=...)` explicitly requests a
`MixedSequentialHistory` whose immutable `task_history` is a bounded native
structural closure. The ordinary call (without task_id) returns the original
`SequentialHistory` on its original path. No mounted caller supplies task_id.
`read_selected_mixed_history_uncommitted` requires the caller's original lock,
connection and transaction and uses the same complete oracle, not a weaker
incremental lifecycle check. No engine/cancellation/startup consumer is attached.

All original combined-schema, sequential replay, ownership, retained document
and old Coding validation finishes before the optional closure is acquired in
the SAME snapshot. The closure follows parent, child and workflow-member edges
to a fixed point, including completed/failed/cancelled/superseded intermediate
tasks and dependencies. It is an inventory, never the set of tasks permitted to
receive a live cancellation write. Cross-root workflow links retain all native
roots; sequential versus retained instance namespaces remain distinct even if
IDs have the same text. Parent cycles, missing parent/member tasks, missing or
foreign marks, dual ownership, invalid types/identities and excess membership
refuse without partial results. Revisit/chain/fanout JSON and native process/job
ownership are not interpreted by this structural interface; their existing
owners remain controlling, and U6b must compose them where required.

Native tasks retain all columns in the existing native task projection as exact
SQLite values in frozen tuples, including NULL, SID, PID, status, notes, budget
and revision fields. Callback references retain the original positive INTEGER
id/task/principal/SID/status. They never select a newest result, coerce a string
id or require today's roster/current SID to rewrite historical attribution.
`MixedTaskHistory.result` matches the exact quartet; it does not authenticate
callback payload, successful host/outer settlement, effective access or effects.
U5b2b2 must still read the original PK/full callback and its original proofs.
Callback payload text is deliberately neither decoded nor copied by U6a; every
retained graph and callback byte stays with its original complete reader/owner.

The optional closure charges the existing D2 C budget and SQL progress hook,
after retained L work has already charged C. A/B/C/L row/byte/step/SQL caps and
one original deadline remain unchanged. The 512-distinct-task ceiling is an
additional membership refusal, not a promise that arbitrary 512-task payloads
fit. Repeated reads/edges and rejected candidates consume their original work.
There is no budget initializer in the closure, retry, slicing fallback, cache,
transaction control, DML, current-authority or filesystem/network observation.

| Owner | Caller / preservation boundary |
| --- | --- |
| `sequential_history.read_sequential_history` optional task_id | Existing callers omit it; complete combined oracle then closure under original C. |
| `sequential_mixed_history.close_mixed_history_uncommitted` | Only explicit complete-reader composition; requires already validated full combined history and original allowance/hook. |
| `read_selected_mixed_history_uncommitted` | Unmounted selected interface, same full oracle/snapshot; no cancellation or callback entitlement. |
| Native task columns/types; `_bounded_rows`, `_structure_cells`, `_binding_owner` | Reuse exact retained projections, pre-fetch gates and ownership relation discriminator; no code moved. |
| Historical Coding cancellation/return and document readers | Byte-identical; no new family interpreter or fallback. |
| Dispatcher/engine/business-Held, U4/U5, Pause, task-cancel route, native root-tail | Byte-identical; U6b must wire its own coherent producers later. |

## U6b producer contract (still required)

A winning business stop must fence fresh visits, result advancing, publication
and Final action. Only an original already-COMMITTED exact callback may drain
unchanged. Preserve original audit/CAS ordering; invoke opaque native Stop only
after COMMIT and outside locks. Cancellation never proves execution/effect
closure: retained prepared/running/unknown ownership, original local action and
receipt uncertainty, budgets, failed tasks and immutable returned/cancelled
histories remain with their original owners. Launcher TERM/INT, ended SID,
missing PID/control or a successful Stop request supplies no product cancellation
or native settlement proof. No Retry/Resume/Reassign/Merge-again/cleanup endpoint.

The next coherent producer unit must independently inventory each changed
entry/caller, use complete/selected mixed closure before writes, retain exact old
reader behavior, close deep terminal dependencies without mutating retained
graphs, and compose the native root-tail consumer. A partially wired producer
must remain unmounted. U6a alone does not discharge that work or release U5/U7.

## Evidence and preserved boundaries

Out-of-repo diagnostics use actual source readers and disposable copied SQLite
histories, with explicitly synthetic native/authority/effect scaffolding. They
are not V3/native acceptance. Handoff/PR record exact B/H, full file/AST and
caller inventory, protected-owner comparisons, zero-write observations,
commands/exits and the exact-head local-CI job receipt. No repository tests are
added. Python units RETIRED, integration SKIPPED THR-243seq42, Product E2E not
triggered; actual mounted mixed cancellation/native races, transport, callback
mount, V3/V5/M4, live access/merge and deployment remain NOT RUN.

Preserve TASK-14478/14531/14559/14572/14627/14632/14640/15049/15055/15074/15164
failures and TASK-15175/15246/15272 REQUEST_CHANGES history; no allowance renewal.
V3 ends 2026-11-23T08:48:38Z, extension Founder-reserved. Live config/admission/
roles/install/migration/deploy/restart/genuine Coding scratch/effect recovery
remain reserved; PR1252 held/untouched, notifications excluded. F-1=(i),
Decision1=B (48 KiB JSON), Decision2=1, SB-1=(a). ACCESS-1 is current authority
only in the daemon and selected-agent own live operations for effective access;
no daemon GitHub/network I/O or access observer. TASK-15250 Pause generation and
TASK-15275 receipt-recording uncertainty semantics remain unchanged.
Merged != deployed. Independent review/QA and manager guarded merge remain.


## U6b1 split declared before edits (TASK-15301)

Fetched B=`b089e8512a5661ab62ac8a1ca2cf3d1538dbf2f2`. The authorized
records/reader/producer split delivers an **unmounted producer interface** in
`sequential_mixed_cancel.py`. U6b2 still owns coherent live retained-family writer
composition, authenticated cross-family ordinary overlays, positive-original-owner
native-tail disposition and Founder cancel/cascade/native-tail attachment. No
partial producer is mounted. Prior U6a paragraphs describe its delivered head;
its readers and all original mounted cancellation paths remain byte-identical.

Assumptions before editing: no schema change; no new route/CLI/transport or public
command; existing old Coding/document writers/readers and U4/U5/Pause/dispatcher/
engine/business-Held/Final receipt owners remain byte-identical. No code is moved.
This producer reuses their interfaces without broadening their acceptance. If an
ordinary ancestor change conflicts with a retained owner's frozen capture, the
complete target oracle refuses and the whole transaction rolls back. U6b2 must
solve that composition at original owner boundaries before mounting anything.

The new coroutine captures current authority before coroutine locks, uses the
original admission immediate writer, and reads complete mixed history before
live membership discovery. The normal live descendant walk stops at terminal
ordinary rows; separate terminal dependency traversal reaches retained sequential
families. Fresh sequential family members expand atomically; hidden live work
under a terminal dependency refuses. Live retained document/Coding rows refuse.
Ordinary point stops refuse sequential/Coding-dependent ancestry. Returned and
cancelled families receive no new fence, task write or control. Each fresh run
uses the original engine CAS fence/event; live task overlays retain the existing
audit shape. A fresh exact native comparison after opaque control discovery and
the complete post-write oracle reject drift. Post-write native task comparison
permits only the derived cancellation fields on the exact live write set;
original result identities, owners, parents and all other native fields match.

Each complete read keeps D2 A/B/C/L limits; both reads share the original
operation deadline and retained-family cumulative allowance. Extra discovery,
copying, native comparison and control collection use one additional finite
operation budget/deadline, with no reset on refusal. Existing run event/visit/
epoch/return limits and native attempt budgets stay with their original owners.
The coroutine performs no native control or external effect. It returns controls
only for newly cancelled live rows, with original root transition captures kept
through COMMIT. Original journal/control/PID uncertainty is inspected without
clearing ownership; retained cancelled sequential families conservatively keep
teardown-incomplete until U6b2 can compose positive original-owner evidence.
No safe cleanup or remote-effect clearance follows from a successful return.

| New owner | Consumers / boundary |
| --- | --- |
| `_tasks`, `_affected` | Exact native projection and bounded write-set selection under the full oracle. Not a general history reader. |
| `_unchanged` | Pre-write native comparison after control discovery, original transaction/budget. |
| `cancel_mixed` | No production importer. Internal producer only, returns the existing cancellation delivery shape; U6b2 owns route/native-tail attachment. |
| Original U6a, U4/U5, old Coding/document, engine/dispatcher/business-Held/Pause/Final owners | Byte/AST-identical; no code moved or new entitlement. |

Evidence is out-of-repo synthetic, not V3/native acceptance. Exact-H source imports,
counters, callback drain/refusal, actual producer ancestor/deep-document/terminal
cases, uncertainty and rollback observations are recorded in TASK-15301 handoff.
Mounted Founder mixed cancellation, live old-Coding/document composition, full
populated old-Coding lifecycle acceptance, native Stop/root-tail integration,
real concurrency/host/outer/effect proof, U5b2b2/U7, V3/V5/M4 and all live operations
remain NOT RUN/implementation as applicable. No repository tests are added.
PRD v1.0.4/msg131 ACCESS-1 and every preserved boundary above remain controlling.


## U6b2a split declared before edits (TASK-15325)

Fetched B=`448d606158f0871300b9240a0c575d8036f58da3`. This session delivers
**U6b2a, an UNMOUNTED original-owner native-tail disposition interface**;
U6b2 is not complete. Existing old Coding cancellation owns its immediate
transaction/COMMIT and freezes ancestor overlays in its original command/event
families; U6b1 owns an admission transaction. Calling those writers inside one
another cannot compose live families while preserving those readers. The split
was declared before edits; no partial mixed path is mounted.

`sequential_cancellation_tail.cancelled_owner_disposition_uncommitted` qualifies
ONE original sequential source/visit in a caller-owned DB lock/snapshot. Its
complete U6a oracle precedes selection; saved run/task/principal/SID, original
TaskInvocation, native entry/fingerprint/root, terminal request, host receipt and
same-owner close/release must agree. Only actual native close/removal, reservation
release and safe host/outer observations qualify. Missing observations, prepared/
unknown ownership, sticky execution uncertainty, survivors, absent original host,
failed outer cleanup, remaining native journal or live tracker/control/PID return
unsettled. Malformed history/identity or drift refuses. Native generation is the
original admission generation, which may precede current Pause generation.
The reader clears no tracker/journal/reservation, calls no Stop and writes no SQL.
Native reason remains unchanged (including cancellation/failure); qualification
is neither successful business completion nor remote-effect clearance.

Caller must supply its original read/retained allowances and deadline; this
interface creates none and owns no transaction/progress hook across external
work. Complete D2 A/B/C/L caps remain unchanged; supplied native observations
share the existing 16 KiB metadata ceiling and original caller copy/traversal
budget. Exhaustion refuses, never partial settlement. The frozen result is an
ephemeral same-call observation, not a durable or reusable receipt. A positive
result qualifies ONE original owner only: never use it to clear an aggregate
teardown-incomplete flag, bypass root protection or infer other owners settled.

| New owner / callers | Boundary |
| --- | --- |
| `CancelledOwnerDisposition` | Immutable one-owner identity/settled observation; no clearance/capability/receipt. |
| `_selected` | Complete combined mixed history and exact original source/visit binding. |
| `cancelled_owner_disposition_uncommitted` | No production caller. Future native settle-live composition must supply actual original observations and aggregate every captured owner. |
| Existing U4/U5/U6a/U6b1, old Coding/document, Pause, engine/dispatcher/business-Held, Final receipt uncertainty, Founder/root-tail consumers | Byte/AST-identical. No moved code, schema, route, transport or configuration change. |

**U6b2b remains IMPLEMENTATION:** live retained old-Coding/document writer and
cross-family ordinary-overlay composition without double-fencing, original-owner
aggregation/durable history composition (including unsettled owners without
handles), and complete Founder cancel/cascade/native-root-tail attachment.
The existing route still calls `cancel_selected` directly or refuses mixed
ordinary ancestry before the old Coding/ordinary path; document cancellation
retains its original owner. U6b1 still has no production importer. Thus the
requested mounted mixed cancellation diagnostic is NOT RUN, not a source or
native acceptance claim. The one-owner interface does not discharge it.

Out-of-repo actual-code diagnostics are SYNTHETIC: native host/authority/settlement
scaffolding, zero-write/refusal/positive-owner observations and existing mounted
selected cancellation evidence are recorded in the handoff/PR, with exact B/H,
protected bytes/AST, callers/global closure and authenticated exact-H local-CI job.
No repository tests. U5b2b2 callback validation/mount, U7 transport, U8/U9 and
Final success Held remain unchanged; V3/native/mixed writer acceptance, V5/M4,
real access/merge/effect recovery and all reserved live operations remain NOT RUN.
All earlier preserved failures, allowances, V3 end and ACCESS-1 limits above apply.
